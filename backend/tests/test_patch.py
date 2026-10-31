import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
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
        solution=[Field(text="Autonomous agent that creates PPTX decks", source=Source.REPO, evidence="backend/main.py")],
        features=[Field(text="Architecture diagram generation", source=Source.REPO, evidence="backend/diagram.py")],
        stack=[Field(text="FastAPI backend", source=Source.REPO, evidence="pyproject.toml")],
        demo_steps=[Field(text="Upload repo and view slides", source=Source.VOICE)],
        impact=[Field(text="10x faster pitch deck iteration", source=Source.VOICE)],
        roadmap=[Field(text="Real-time multi-agent collaboration", source=Source.VOICE)],
        components=graph,
    )


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    templates_dir = tmp_path / "templates"
    storage_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_dir", storage_dir)
    monkeypatch.setattr(type(settings), "templates_dir", property(lambda self: templates_dir))

    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()

    import app.db as db_mod
    db_mod._engine = None
    init_db()

    yield

    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()


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
                name="Patch Test Project",
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


def test_patch_flips_source_to_user_clears_evidence_and_increments_version():
    _create_template("tmpl_1")
    deck = build_sample_deck(template_id="tmpl_1", project_id="p_patch_1")
    # Set bullet 0 of slide 3 (index 2) to needs_input with evidence
    target_slide = deck.slides[2]
    target_slide.bullets[0] = Field(
        text="TODO: verify unverified fact",
        source=Source.NEEDS_INPUT,
        evidence="some/old/path.ts",
    )
    _setup_project_with_version("p_patch_1", 1, deck)

    client = TestClient(app)
    res = client.patch(
        "/api/projects/p_patch_1/deck",
        json={
            "base_version": 1,
            "slide_id": target_slide.id,
            "target": "bullet",
            "bullet_index": 0,
            "text": "Human verified bullet item",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["version"] == 2
    assert data["created_by"] == "manual"
    assert data["summary"] == "Edited slide 3 bullet 0"

    updated_slide = data["deck"]["slides"][2]
    updated_bullet = updated_slide["bullets"][0]
    assert updated_bullet["text"] == "Human verified bullet item"
    assert updated_bullet["source"] == "user"
    assert updated_bullet["evidence"] is None

    # Check DB
    with session_scope() as session:
        v2_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == "p_patch_1",
                DeckVersionRow.version == 2,
            )
        ).first()
        assert v2_row is not None
        assert v2_row.created_by == "manual"
        assert v2_row.summary == "Edited slide 3 bullet 0"


def test_patch_stale_version_409():
    _create_template("tmpl_1")
    deck = build_sample_deck(template_id="tmpl_1", project_id="p_patch_stale")
    _setup_project_with_version("p_patch_stale", 1, deck)

    client = TestClient(app)
    res = client.patch(
        "/api/projects/p_patch_stale/deck",
        json={
            "base_version": 0,  # Stale: current is 1
            "slide_id": deck.slides[0].id,
            "target": "title",
            "text": "New Title",
        },
    )
    assert res.status_code == 409
    data = res.json()
    assert data["code"] == "STALE_VERSION"


def test_patch_over_limit_text_truncated_with_summary_noting_it():
    _create_template("tmpl_1")
    deck = build_sample_deck(template_id="tmpl_1", project_id="p_patch_fit")
    target_slide = deck.slides[1]  # Problem slide
    _setup_project_with_version("p_patch_fit", 1, deck)

    # Bullet max limit is 100 characters. Provide 150 characters.
    long_text = "This is a very long text string that contains many words and is definitely going to exceed the one hundred character limit defined by the template manifest."
    assert len(long_text) > 100

    client = TestClient(app)
    res = client.patch(
        "/api/projects/p_patch_fit/deck",
        json={
            "base_version": 1,
            "slide_id": target_slide.id,
            "target": "bullet",
            "bullet_index": 0,
            "text": long_text,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["version"] == 2
    assert "(1 fields truncated)" in data["summary"]

    updated_bullet = data["deck"]["slides"][1]["bullets"][0]
    assert len(updated_bullet["text"]) <= 100
    assert updated_bullet["text"].endswith("...")
    assert updated_bullet["source"] == "user"
    assert updated_bullet["evidence"] is None


@pytest.mark.asyncio
async def test_patch_during_running_job_409():
    _create_template("tmpl_1")
    deck = build_sample_deck(template_id="tmpl_1", project_id="p_patch_busy")
    _setup_project_with_version("p_patch_busy", 1, deck)

    # Lock project as if a job is running
    await try_acquire("p_patch_busy")

    try:
        client = TestClient(app)
        res = client.patch(
            "/api/projects/p_patch_busy/deck",
            json={
                "base_version": 1,
                "slide_id": deck.slides[0].id,
                "target": "title",
                "text": "Title during job",
            },
        )
        assert res.status_code == 409
        data = res.json()
        assert data["code"] == "JOB_IN_PROGRESS"
    finally:
        release("p_patch_busy")


def test_patch_not_found_errors():
    _create_template("tmpl_1")
    deck = build_sample_deck(template_id="tmpl_1", project_id="p_patch_nf")
    _setup_project_with_version("p_patch_nf", 1, deck)

    client = TestClient(app)

    # Unknown project
    res1 = client.patch(
        "/api/projects/unknown_project/deck",
        json={
            "base_version": 1,
            "slide_id": deck.slides[0].id,
            "target": "title",
            "text": "Foo",
        },
    )
    assert res1.status_code == 404

    # Unknown slide_id
    res2 = client.patch(
        "/api/projects/p_patch_nf/deck",
        json={
            "base_version": 1,
            "slide_id": "non_existent_slide_id",
            "target": "title",
            "text": "Foo",
        },
    )
    assert res2.status_code == 404

    # Invalid bullet_index
    res3 = client.patch(
        "/api/projects/p_patch_nf/deck",
        json={
            "base_version": 1,
            "slide_id": deck.slides[0].id,
            "target": "bullet",
            "bullet_index": 999,
            "text": "Foo",
        },
    )
    assert res3.status_code == 404


def test_patch_layout_style():
    _create_template("tmpl_1")
    deck = build_sample_deck(template_id="tmpl_1", project_id="p_patch_layout")
    _setup_project_with_version("p_patch_layout", 1, deck)

    client = TestClient(app)
    slide_id = deck.slides[2].id

    # Patch layout_style to vertical
    res = client.patch(
        "/api/projects/p_patch_layout/deck",
        json={
            "base_version": 1,
            "slide_id": slide_id,
            "target": "layout_style",
            "text": "vertical",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == 2
    slide2 = next(s for s in data["deck"]["slides"] if s["id"] == slide_id)
    assert slide2["layout_style"] == "vertical"

    # Patch layout_style to horizontal
    res2 = client.patch(
        "/api/projects/p_patch_layout/deck",
        json={
            "base_version": 2,
            "slide_id": slide_id,
            "target": "layout_style",
            "text": "horizontal",
        },
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["version"] == 3
    slide2_h = next(s for s in data2["deck"]["slides"] if s["id"] == slide_id)
    assert slide2_h["layout_style"] == "horizontal"
