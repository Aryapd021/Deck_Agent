import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import httpx
from pptx import Presentation
from sqlmodel import select

from app.config import get_settings
from app.core.jobs import (
    JobContext,
    JobResult,
    _get_lock,
    _job_events_cache,
    _job_seqs,
    _job_subscribers,
    _job_versions,
    _project_locks,
    _running_tasks,
    event_stream,
    release,
    try_acquire,
)
from app.core.storage import rel_to_storage, version_dir
from app.db import init_db, session_scope
from app.main import app
from app.models.api import EditRequest, FitLimits, TemplateManifest
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.edit_ops import (
    DeleteSlide,
    EditPlan,
    MoveSlide,
    SetDiagramFocus,
    SlidePatch,
    UpdateSlide,
)
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.models.tables import (
    ArtifactRow,
    DeckVersionRow,
    JobEventRow,
    JobRow,
    ProjectInputRow,
    ProjectRow,
    TemplateRow,
)
from app.services.llm import base as llm_base
from app.services.llm.base import LLMProvider
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck

VALID_PNG_RED = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\n\x00\x00\x00\n\x08\x02\x00\x00\x00"
    b"\x02PX\xea\x00\x00\x00\x13IDATx\x9cc\xfc\xcf\x80\x0f0\xe1\x95e\x18\xa9\xd2\x00A,\x01\x13y\xed\xba&\x00\x00\x00\x00IEND\xaeB`\x82"
)

VALID_PNG_BLUE = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x14\x00\x00\x00\x14\x08\x02\x00\x00\x00"
    b"\x02\xeb\x8aZ\x00\x00\x00,IDATx\x9ccd`\xf8\xcf@.`\"[\'\xc3\xa8f\x92\x01\x13\xe9Z\x10`T3\x89\x80\x89T\r\xc8`T3\x89\x80\xa2\x00\x03\x00 \x9e\x01\'\x04\xb9P\x8e\x00\x00\x00\x00IEND\xaeB`\x82"
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


class EditFakeProvider(LLMProvider):
    def __init__(self, plan: EditPlan | None = None) -> None:
        self.plan = plan

    async def complete_json(self, prompt: str) -> str:
        if self.plan is not None:
            return self.plan.model_dump_json()
        return '{"ops":[],"message":"No edits"}'


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


def _setup_project_with_version_1(
    project_id: str = "proj_edit_test",
    template_id: str = "tmpl_1",
    custom_diagram_bytes: bytes = VALID_PNG_RED,
) -> tuple[Deck, ProjectFacts]:
    _create_template(template_id)
    deck = build_sample_deck(template_id, project_id)
    deck.version = 1
    facts = _make_dummy_facts()

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        proj = ProjectRow(
            id=project_id,
            name="Edit Test Project",
            repo_ref="./",
            template_id=template_id,
            created_at=now,
        )
        session.add(proj)
        session.flush()

        v1_row = DeckVersionRow(
            project_id=project_id,
            version=1,
            deck_json=deck.model_dump_json(),
            created_by="generate",
            summary="Initial version",
            created_at=now,
        )
        session.add(v1_row)

    v1_dir = version_dir(project_id, 1)
    v1_dir.mkdir(parents=True, exist_ok=True)
    (v1_dir / "deck.json").write_text(deck.model_dump_json(), encoding="utf-8")
    (v1_dir / "facts.json").write_text(facts.model_dump_json(), encoding="utf-8")
    (v1_dir / "deck.pptx").write_bytes(b"PPTX_CONTENT")
    (v1_dir / "diagram.svg").write_text("<svg>original</svg>", encoding="utf-8")
    (v1_dir / "diagram.png").write_bytes(custom_diagram_bytes)
    slides_dir = v1_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    (slides_dir / "1.png").write_bytes(b"SLIDE_1_PNG")
    (slides_dir / "2.png").write_bytes(b"SLIDE_2_PNG")

    with session_scope() as session:
        session.add(ArtifactRow(project_id=project_id, version=1, kind="pptx", path=rel_to_storage(v1_dir / "deck.pptx")))
        session.add(ArtifactRow(project_id=project_id, version=1, kind="diagram", path=rel_to_storage(v1_dir / "diagram.png")))
        session.add(ArtifactRow(project_id=project_id, version=1, kind="slide_png", path=rel_to_storage(slides_dir / "1.png")))
        session.add(ArtifactRow(project_id=project_id, version=1, kind="slide_png", path=rel_to_storage(slides_dir / "2.png")))

    return deck, facts


async def _collect_events(job_id: str) -> list[dict]:
    events = []
    async for chunk in event_stream(job_id):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                events.append(payload)
    return events


@pytest.mark.asyncio
async def test_shorten_and_move_produces_v2(monkeypatch: pytest.MonkeyPatch):
    project_id = "proj_shorten_move"
    deck, facts = _setup_project_with_version_1(project_id)

    slide3 = deck.slides[2]
    demo_slide = next(s for s in deck.slides if s.type == SlideType.DEMO)

    plan = EditPlan(
        ops=[
            UpdateSlide(
                op="update_slide",
                slide_id=slide3.id,
                fields=SlidePatch(
                    bullets=[Field(text="Short bullet 1", source=Source.USER)]
                ),
            ),
            MoveSlide(
                op="move_slide",
                slide_id=demo_slide.id,
                to_index=1,
            ),
        ],
        message=None,
    )
    fake_provider = EditFakeProvider(plan)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake_provider)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/projects/{project_id}/edit",
            json={"command": "Shorten slide 3 and move demo up", "base_version": 1},
        )
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        events = await _collect_events(job_id)

    stages = [e["stage"] for e in events]
    # Expect interpret, validate, apply, render, preview, done
    assert "interpret" in stages
    assert "validate" in stages
    assert "apply" in stages
    assert "render" in stages
    assert "preview" in stages
    assert stages[-1] == "done"

    terminal = events[-1]
    assert terminal["status"] == "done"
    assert terminal["version"] == 2
    assert "Slide 3: 3 bullets to 1" in terminal["message"]
    assert "to position 2" in terminal["message"]

    # Verify DB DeckVersionRow
    with session_scope() as session:
        v2_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == project_id,
                DeckVersionRow.version == 2,
            )
        ).first()
        assert v2_row is not None
        assert v2_row.created_by == "edit"
        assert v2_row.summary == terminal["message"]

    # Verify disk files
    v2_dir = version_dir(project_id, 2)
    assert (v2_dir / "deck.json").exists()
    assert (v2_dir / "deck.pptx").exists()
    assert (v2_dir / "facts.json").exists()
    assert (v2_dir / "diagram.png").exists()


@pytest.mark.asyncio
async def test_empty_ops_returns_done_with_version_null(monkeypatch: pytest.MonkeyPatch):
    project_id = "proj_empty_ops"
    _setup_project_with_version_1(project_id)

    plan = EditPlan(ops=[], message="I can't do that yet.")
    fake_provider = EditFakeProvider(plan)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake_provider)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/projects/{project_id}/edit",
            json={"command": "Do something out of scope", "base_version": 1},
        )
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        events = await _collect_events(job_id)

    terminal = events[-1]
    assert terminal["stage"] == "done"
    assert terminal["status"] == "done"
    assert terminal["version"] is None
    assert terminal["message"] == "I can't do that yet."

    # Verify no v2 in DB and no v2 on disk
    with session_scope() as session:
        v2_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == project_id,
                DeckVersionRow.version == 2,
            )
        ).first()
        assert v2_row is None

    assert not version_dir(project_id, 2).exists()


@pytest.mark.asyncio
async def test_edit_rejected_returns_done_with_version_null(monkeypatch: pytest.MonkeyPatch):
    project_id = "proj_rejected"
    _setup_project_with_version_1(project_id)

    plan = EditPlan(
        ops=[
            UpdateSlide(
                op="update_slide",
                slide_id="nonexistent_slide_id",
                fields=SlidePatch(title=Field(text="Bad Slide", source=Source.USER)),
            )
        ],
        message=None,
    )
    fake_provider = EditFakeProvider(plan)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake_provider)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/projects/{project_id}/edit",
            json={"command": "Change missing slide", "base_version": 1},
        )
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        events = await _collect_events(job_id)

    terminal = events[-1]
    assert terminal["stage"] == "done"
    assert terminal["status"] == "done"
    assert terminal["version"] is None
    assert "Couldn't apply that:" in terminal["message"]
    assert "Nothing was changed." in terminal["message"]

    # Verify no v2 created
    with session_scope() as session:
        v2_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == project_id,
                DeckVersionRow.version == 2,
            )
        ).first()
        assert v2_row is None

    assert not version_dir(project_id, 2).exists()


def test_stale_base_version_and_lock_freed():
    project_id = "proj_stale"
    _setup_project_with_version_1(project_id)

    client = TestClient(app)
    res = client.post(
        f"/api/projects/{project_id}/edit",
        json={"command": "Edit deck", "base_version": 99},
    )
    assert res.status_code == 409
    data = res.json()
    assert data["code"] == "STALE_VERSION"
    assert data["detail"]["current_version"] == 1
    assert data["detail"]["base_version"] == 99

    # Verify the lock is free afterwards
    lock = _get_lock(project_id)
    assert not lock.locked()


@pytest.mark.asyncio
async def test_edit_while_generate_runs_returns_409():
    project_id = "proj_in_progress"
    _setup_project_with_version_1(project_id)

    # Acquire lock to simulate a running generate job
    await try_acquire(project_id)
    try:
        client = TestClient(app)
        res = client.post(
            f"/api/projects/{project_id}/edit",
            json={"command": "Edit deck", "base_version": 1},
        )
        assert res.status_code == 409
        data = res.json()
        assert data["code"] == "JOB_IN_PROGRESS"
    finally:
        release(project_id)


@pytest.mark.asyncio
async def test_diagram_focus_re_renders_diagram_while_other_edits_keep_bytes(monkeypatch: pytest.MonkeyPatch):
    project_id = "proj_diagram_focus"
    deck, facts = _setup_project_with_version_1(project_id, custom_diagram_bytes=VALID_PNG_RED)

    # 1. Non-diagram edit: update slide 1 title
    slide1 = deck.slides[0]
    plan_normal = EditPlan(
        ops=[
            UpdateSlide(
                op="update_slide",
                slide_id=slide1.id,
                fields=SlidePatch(title=Field(text="Renamed Title", source=Source.USER)),
            )
        ],
        message=None,
    )
    monkeypatch.setattr(llm_base, "get_provider", lambda: EditFakeProvider(plan_normal))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/projects/{project_id}/edit",
            json={"command": "Rename title", "base_version": 1},
        )
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        events = await _collect_events(job_id)

    stages = [e["stage"] for e in events]
    assert "diagram" not in stages

    v2_dir = version_dir(project_id, 2)
    assert (v2_dir / "diagram.png").read_bytes() == VALID_PNG_RED

    # 2. Diagram focus edit on v2: SetDiagramFocus
    def mock_render_diagram(graph, palette, out_dir):
        (out_dir / "diagram.svg").write_text("<svg>new</svg>", encoding="utf-8")
        (out_dir / "diagram.png").write_bytes(VALID_PNG_BLUE)
        return (out_dir / "diagram.svg", out_dir / "diagram.png")

    monkeypatch.setattr("app.core.jobs.render_diagram", mock_render_diagram)

    plan_focus = EditPlan(
        ops=[
            SetDiagramFocus(
                op="set_diagram_focus",
                node_ids=["frontend", "backend", "db", "ai"],
            )
        ],
        message=None,
    )
    monkeypatch.setattr(llm_base, "get_provider", lambda: EditFakeProvider(plan_focus))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        res2 = await ac.post(
            f"/api/projects/{project_id}/edit",
            json={"command": "Focus diagram on 4 components", "base_version": 2},
        )
        assert res2.status_code == 202
        job_id2 = res2.json()["job_id"]

        events2 = await _collect_events(job_id2)

    stages2 = [e["stage"] for e in events2]
    assert "diagram" in stages2

    v3_dir = version_dir(project_id, 3)
    assert (v3_dir / "diagram.png").read_bytes() == VALID_PNG_BLUE
    assert (v3_dir / "diagram.png").read_bytes() != VALID_PNG_RED
