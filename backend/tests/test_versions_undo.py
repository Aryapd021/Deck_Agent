import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import httpx
from pptx import Presentation
from sqlmodel import select

from app.config import get_settings
from app.core.jobs import (
    _get_lock,
    _job_events_cache,
    _job_seqs,
    _job_subscribers,
    _job_versions,
    _project_locks,
    _running_tasks,
    release,
    try_acquire,
)
from app.core.storage import rel_to_storage, version_dir
from app.db import init_db, session_scope
from app.main import app
from app.models.api import FitLimits, TemplateManifest
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.models.tables import (
    ArtifactRow,
    DeckVersionRow,
    ProjectRow,
    TemplateRow,
)
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck

VALID_PNG_RED = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\n\x00\x00\x00\n\x08\x02\x00\x00\x00"
    b"\x02PX\xea\x00\x00\x00\x13IDATx\x9cc\xfc\xcf\x80\x0f0\xe1\x95e\x18\xa9\xd2\x00A,\x01\x13y\xed\xba&\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_dummy_facts() -> ProjectFacts:
    graph = ComponentGraph(
        nodes=[
            Node(id="frontend", label="Frontend UI", kind=NodeKind.FRONTEND),
            Node(id="backend", label="API Backend", kind=NodeKind.BACKEND),
            Node(id="db", label="Postgres DB", kind=NodeKind.DB),
            Node(id="ai", label="LLM Service", kind=NodeKind.AI),
        ],
        edges=[
            Edge(from_="frontend", to="backend", label="HTTP"),
            Edge(from_="backend", to="db", label="SQL"),
            Edge(from_="backend", to="ai", label="Prompt"),
        ],
    )
    return ProjectFacts(
        name="fixture_repo",
        one_liner=Field(text="A test fixture repo for autonomous pitch decks", source=Source.REPO, evidence="README.md"),
        problem=[Field(text="Creating pitch decks manually is slow", source=Source.CHAT)],
        solution=[Field(text="Automated slide creation", source=Source.VOICE)],
        features=[Field(text="Fast rendering pipeline", source=Source.REPO, evidence="src/main.py")],
        stack=[Field(text="Python and FastAPI", source=Source.REPO, evidence="pyproject.toml")],
        demo_steps=[Field(text="Run generator", source=Source.REPO, evidence="src/main.py")],
        impact=[Field(text="10x faster generation", source=Source.NEEDS_INPUT)],
        roadmap=[Field(text="Add more export formats", source=Source.REPO, evidence="README.md")],
        components=graph,
    )


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    test_templates = tmp_path / "templates"
    test_templates.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)
    monkeypatch.setattr(type(get_settings()), "templates_dir", property(lambda self: test_templates))

    import app.db as db_mod
    db_mod._engine = None
    init_db()

    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()

    yield

    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()
    db_mod._engine = None


def _create_template(template_id: str = "tmpl_1") -> tuple[Path, TemplateManifest]:
    settings = get_settings()
    template_path = settings.templates_dir / f"{template_id}.pptx"
    prs = Presentation()
    prs.save(str(template_path))

    manifest = inspect_template(template_path, template_id, "Default Template")
    manifest.limits = {
        st.value: FitLimits(title_max=70, bullet_max=100, bullets_max=5, notes_max=600)
        for st in SlideType
    }
    with session_scope() as session:
        tmpl_row = TemplateRow(
            id=template_id,
            name="Default Template",
            path=f"templates/{template_id}.pptx",
            manifest_json=manifest.model_dump_json(),
            created_at=datetime.now(timezone.utc),
        )
        session.add(tmpl_row)

    return template_path, manifest


def _setup_project_with_version(
    project_id: str,
    version: int,
    deck: Deck,
    created_by: str = "generate",
    summary: str = "Initial version",
) -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        proj = session.get(ProjectRow, project_id)
        if not proj:
            proj = ProjectRow(
                id=project_id,
                name="Undo Test Project",
                repo_ref="./",
                template_id="tmpl_1",
                created_at=now,
            )
            session.add(proj)
            session.flush()

        v_row = DeckVersionRow(
            project_id=project_id,
            version=version,
            deck_json=deck.model_dump_json(),
            created_by=created_by,
            summary=summary,
            created_at=now,
        )
        session.add(v_row)

    v_dir = version_dir(project_id, version)
    v_dir.mkdir(parents=True, exist_ok=True)
    facts = _make_dummy_facts()
    (v_dir / "deck.json").write_text(deck.model_dump_json(), encoding="utf-8")
    (v_dir / "facts.json").write_text(facts.model_dump_json(), encoding="utf-8")
    (v_dir / "deck.pptx").write_bytes(b"PPTX_CONTENT")
    (v_dir / "diagram.svg").write_text("<svg>diagram</svg>", encoding="utf-8")
    (v_dir / "diagram.png").write_bytes(VALID_PNG_RED)
    slides_dir = v_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    (slides_dir / "1.png").write_bytes(b"SLIDE_1_PNG")

    with session_scope() as session:
        session.add(ArtifactRow(project_id=project_id, version=version, kind="pptx", path=rel_to_storage(v_dir / "deck.pptx")))
        session.add(ArtifactRow(project_id=project_id, version=version, kind="diagram", path=rel_to_storage(v_dir / "diagram.png")))
        session.add(ArtifactRow(project_id=project_id, version=version, kind="slide_png", path=rel_to_storage(slides_dir / "1.png")))


def test_versions_list_and_not_found():
    _create_template("tmpl_1")
    client = TestClient(app)

    # 1. 404 for unknown project
    res = client.get("/api/projects/unknown_proj/versions")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"

    # 2. Existing project with v1
    project_id = "proj_versions_test"
    deck1 = build_sample_deck("tmpl_1", project_id)
    deck1.version = 1
    _setup_project_with_version(project_id, 1, deck1, created_by="generate", summary="Initial deck")

    res = client.get(f"/api/projects/{project_id}/versions")
    assert res.status_code == 200
    versions = res.json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["created_by"] == "generate"
    assert versions[0]["summary"] == "Initial deck"
    assert "created_at" in versions[0]


def test_undo_at_v1_raises_404():
    _create_template("tmpl_1")
    project_id = "proj_undo_v1"
    deck1 = build_sample_deck("tmpl_1", project_id)
    deck1.version = 1
    _setup_project_with_version(project_id, 1, deck1, created_by="generate", summary="Initial deck")

    client = TestClient(app)
    res = client.post(f"/api/projects/{project_id}/undo")
    assert res.status_code == 404
    data = res.json()
    assert data["code"] == "NOT_FOUND"
    assert "Nothing to undo" in data["message"]

    # Verify lock is free
    assert not _get_lock(project_id).locked()


@pytest.mark.asyncio
async def test_undo_during_running_job_raises_409():
    _create_template("tmpl_1")
    project_id = "proj_undo_locked"
    deck1 = build_sample_deck("tmpl_1", project_id)
    deck1.version = 1
    _setup_project_with_version(project_id, 1, deck1)
    deck2 = deck1.model_copy(deep=True)
    deck2.version = 2
    _setup_project_with_version(project_id, 2, deck2)

    await try_acquire(project_id)
    try:
        client = TestClient(app)
        res = client.post(f"/api/projects/{project_id}/undo")
        assert res.status_code == 409
        data = res.json()
        assert data["code"] == "JOB_IN_PROGRESS"
    finally:
        release(project_id)


def test_edit_then_undo_yields_new_version_with_previous_content():
    _create_template("tmpl_1")
    project_id = "proj_undo_workflow"
    deck1 = build_sample_deck("tmpl_1", project_id)
    deck1.version = 1
    _setup_project_with_version(project_id, 1, deck1, created_by="generate", summary="Initial generated deck")

    # Simulate edit creating v2
    deck2 = deck1.model_copy(deep=True)
    deck2.version = 2
    deck2.slides[0].title.text = "Completely Edited Title"
    _setup_project_with_version(project_id, 2, deck2, created_by="edit", summary="Edited title")

    client = TestClient(app)

    # Verify 2 versions in history
    res_v = client.get(f"/api/projects/{project_id}/versions")
    assert res_v.status_code == 200
    versions_before = res_v.json()
    assert len(versions_before) == 2
    assert [v["version"] for v in versions_before] == [2, 1]

    # Perform undo: current is 2, source is 1 -> produces v3
    res_undo = client.post(f"/api/projects/{project_id}/undo")
    assert res_undo.status_code == 200
    undo_data = res_undo.json()
    assert undo_data["version"] == 3
    assert undo_data["created_by"] == "undo"
    assert undo_data["summary"] == "Undo: restored v1"

    # Compare deck content (ignoring version field)
    restored_deck = Deck.model_validate(undo_data["deck"])
    assert restored_deck.version == 3
    assert restored_deck.model_dump(exclude={"version"}) == deck1.model_dump(exclude={"version"})

    # Verify files exist in v3 directory
    v3_dir = version_dir(project_id, 3)
    assert (v3_dir / "deck.json").exists()
    assert (v3_dir / "deck.pptx").exists()
    assert (v3_dir / "facts.json").exists()
    assert (v3_dir / "diagram.png").exists()
    assert (v3_dir / "slides" / "1.png").exists()

    # History length grows by one
    res_v2 = client.get(f"/api/projects/{project_id}/versions")
    assert res_v2.status_code == 200
    versions_after = res_v2.json()
    assert len(versions_after) == 3
    assert [v["version"] for v in versions_after] == [3, 2, 1]
    assert [v["created_by"] for v in versions_after] == ["undo", "edit", "generate"]

    # Verify GET /deck returns the highest version (v3)
    res_latest = client.get(f"/api/projects/{project_id}/deck")
    assert res_latest.status_code == 200
    latest_data = res_latest.json()
    assert latest_data["version"] == 3
    assert latest_data["created_by"] == "undo"
    assert latest_data["summary"] == "Undo: restored v1"
    assert latest_data["deck"]["slides"][0]["title"]["text"] == deck1.slides[0].title.text


def test_undo_twice_returns_content_of_original():
    # Section 7 item 9: Undo copies version (current - 1) into a new version, so pressing Undo twice returns to the content of the version prior to the first undo.
    # Specifically:
    # v1 (deck1) -> edit -> v2 (deck2).
    # First undo from v2: current=2, source=1 -> v3 with deck1 content.
    # Second undo from v3: current=3, source=2 -> v4 with deck2 content.
    _create_template("tmpl_1")
    project_id = "proj_undo_twice"
    deck1 = build_sample_deck("tmpl_1", project_id)
    deck1.version = 1
    _setup_project_with_version(project_id, 1, deck1, created_by="generate", summary="Initial deck")

    deck2 = deck1.model_copy(deep=True)
    deck2.version = 2
    deck2.slides[0].title.text = "Modified In V2"
    _setup_project_with_version(project_id, 2, deck2, created_by="edit", summary="Edited title")

    client = TestClient(app)

    # First Undo (current=2 -> restores v1 as v3)
    res1 = client.post(f"/api/projects/{project_id}/undo")
    assert res1.status_code == 200
    v3_deck = Deck.model_validate(res1.json()["deck"])
    assert res1.json()["version"] == 3
    assert res1.json()["summary"] == "Undo: restored v1"
    assert v3_deck.model_dump(exclude={"version"}) == deck1.model_dump(exclude={"version"})

    # Second Undo (current=3 -> restores v2 as v4)
    res2 = client.post(f"/api/projects/{project_id}/undo")
    assert res2.status_code == 200
    v4_deck = Deck.model_validate(res2.json()["deck"])
    assert res2.json()["version"] == 4
    assert res2.json()["summary"] == "Undo: restored v2"
    assert v4_deck.model_dump(exclude={"version"}) == deck2.model_dump(exclude={"version"})

    # Verify history is never rewritten: all 4 versions exist in chronological sequence
    res_versions = client.get(f"/api/projects/{project_id}/versions")
    assert res_versions.status_code == 200
    assert [v["version"] for v in res_versions.json()] == [4, 3, 2, 1]
