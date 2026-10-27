import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from pptx import Presentation
from sqlmodel import select

from app.config import get_settings
from app.core.jobs import (
    JobContext,
    JobResult,
    _job_events_cache,
    _job_seqs,
    _job_subscribers,
    _job_versions,
    _project_locks,
    _running_tasks,
    commit_version,
    event_stream,
    release,
    run_generate,
    start_job,
    try_acquire,
)
from app.core.storage import project_dir, version_dir
from app.db import init_db, session_scope
from app.models.api import FitLimits, SlotMap, TemplateManifest
from app.models.deck import Deck, DeckDraft, Field, Slide, SlideType, Source
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.models.tables import ArtifactRow, DeckVersionRow, JobRow, ProjectInputRow, ProjectRow, TemplateRow
from app.services.llm import base as llm_base
from app.services.llm.base import LLMProvider
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck


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


class PipelineFakeProvider(LLMProvider):
    def __init__(self, sample_deck: Deck) -> None:
        self.sample_deck = sample_deck
        self.dummy_facts = _make_dummy_facts()

    async def complete_json(self, prompt: str) -> str:
        if "ProjectFacts" in prompt:
            return self.dummy_facts.model_dump_json()
        elif "DeckDraft" in prompt:
            return DeckDraft(slides=self.sample_deck.slides).model_dump_json()
        elif "ChatDigest" in prompt:
            return '{"problem_framing":["Framing"],"decisions":["Decided on SQLite"],"rejected_options":[],"insights":[]}'
        return "{}"


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

    # Clear in-memory job tracking structures
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


def _create_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("# fixture_repo\nA test repository", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'fixture-repo'\nversion = '0.1.0'\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text("def main():\n    print('Hello World')\n", encoding="utf-8")
    return repo


def _create_template(template_id: str = "tmpl_1") -> tuple[Path, TemplateManifest]:
    settings = get_settings()
    template_path = settings.templates_dir / f"{template_id}.pptx"
    prs = Presentation()
    prs.save(str(template_path))

    manifest = inspect_template(template_path, template_id, "Default Template")
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


@pytest.mark.asyncio
async def test_full_generate_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_path = _create_fixture_repo(tmp_path)
    template_id = "tmpl_1"
    _create_template(template_id)

    project_id = "proj_gen_test"
    with session_scope() as session:
        proj = ProjectRow(
            id=project_id,
            name="Gen Pipeline Test",
            repo_ref=repo_path.as_posix(),
            template_id=template_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(proj)
        session.flush()
        inp = ProjectInputRow(
            project_id=project_id,
            pitch_notes="We are pitching fixture_repo to angels",
            chat_redacted="Discussion about architecture and roadmap",
            criteria="Cover all features clearly",
            updated_at=datetime.now(timezone.utc),
        )
        session.add(inp)

    sample_deck = build_sample_deck(template_id, project_id)
    fake_provider = PipelineFakeProvider(sample_deck)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake_provider)

    await try_acquire(project_id)
    job_id = await start_job(project_id, "generate", run_generate)

    # Collect SSE chunks
    chunks = []
    async for chunk in event_stream(job_id):
        chunks.append(chunk)

    # Extract stages in order of first appearance
    stages_in_order = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                stage = payload["stage"]
                if not stages_in_order or stages_in_order[-1] != stage:
                    stages_in_order.append(stage)

    # Asserts the event stages arrive in order ingest, redact, digest, facts, deck, render, done
    assert stages_in_order == ["ingest", "redact", "digest", "facts", "deck", "render", "done"]

    # Assert deck_version row and v1/deck.pptx exist
    with session_scope() as session:
        v1_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == project_id,
                DeckVersionRow.version == 1,
            )
        ).first()
        assert v1_row is not None
        assert v1_row.version == 1
        assert v1_row.created_by == "generate"
        assert "Generated deck from fixture_repo" in (v1_row.summary or "")

        # Check ArtifactRow
        artifacts = session.exec(
            select(ArtifactRow).where(
                ArtifactRow.project_id == project_id,
                ArtifactRow.version == 1,
            )
        ).all()
        assert any(a.kind == "pptx" for a in artifacts)

    v1_dir = version_dir(project_id, 1)
    assert (v1_dir / "deck.pptx").exists()
    assert (v1_dir / "deck.json").exists()
    assert (v1_dir / "facts.json").exists()


@pytest.mark.asyncio
async def test_forced_failure_in_render_leaves_no_version_and_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = _create_fixture_repo(tmp_path)
    template_id = "tmpl_1"
    _create_template(template_id)

    project_id = "proj_render_fail"
    with session_scope() as session:
        proj = ProjectRow(
            id=project_id,
            name="Render Fail Test",
            repo_ref=repo_path.as_posix(),
            template_id=template_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(proj)

    sample_deck = build_sample_deck(template_id, project_id)
    fake_provider = PipelineFakeProvider(sample_deck)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake_provider)

    # Monkeypatch render_deck in app.core.jobs to simulate a forced failure during rendering
    def _exploding_render(*args, **kwargs):
        raise RuntimeError("Simulated render explosion")

    monkeypatch.setattr("app.core.jobs.render_deck", _exploding_render)

    await try_acquire(project_id)
    job_id = await start_job(project_id, "generate", run_generate)

    # Wait for the task to finish
    await asyncio.gather(*list(_running_tasks), return_exceptions=True)

    # Assert job recorded as error
    with session_scope() as session:
        job = session.get(JobRow, job_id)
        assert job is not None
        assert job.status == "error"

        # Assert no deck_version row exists
        v_rows = session.exec(
            select(DeckVersionRow).where(DeckVersionRow.project_id == project_id)
        ).all()
        assert len(v_rows) == 0

    # Assert no v1 or tmp dirs exist
    v1_dir = version_dir(project_id, 1)
    assert not v1_dir.exists()

    p_dir = project_dir(project_id)
    if p_dir.exists():
        tmp_dirs = [p for p in p_dir.iterdir() if p.name.startswith("v1.tmp-")]
        assert len(tmp_dirs) == 0


@pytest.mark.asyncio
async def test_sequential_generate_yields_v1_then_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = _create_fixture_repo(tmp_path)
    template_id = "tmpl_1"
    _create_template(template_id)

    project_id = "proj_v1_v2"
    with session_scope() as session:
        proj = ProjectRow(
            id=project_id,
            name="Sequential Test",
            repo_ref=repo_path.as_posix(),
            template_id=template_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(proj)

    sample_deck = build_sample_deck(template_id, project_id)
    fake_provider = PipelineFakeProvider(sample_deck)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake_provider)

    # Run 1 -> v1
    await try_acquire(project_id)
    job1_id = await start_job(project_id, "generate", run_generate)
    await asyncio.gather(*list(_running_tasks))

    with session_scope() as session:
        job1 = session.get(JobRow, job1_id)
        assert job1.status == "done"

    v1_dir = version_dir(project_id, 1)
    assert (v1_dir / "deck.pptx").exists()

    # Run 2 -> v2
    await try_acquire(project_id)
    job2_id = await start_job(project_id, "generate", run_generate)
    await asyncio.gather(*list(_running_tasks))

    with session_scope() as session:
        job2 = session.get(JobRow, job2_id)
        assert job2.status == "done"

        versions = session.exec(
            select(DeckVersionRow.version)
            .where(DeckVersionRow.project_id == project_id)
            .order_by(DeckVersionRow.version.asc())
        ).all()
        assert versions == [1, 2]

    v2_dir = version_dir(project_id, 2)
    assert (v2_dir / "deck.pptx").exists()
    assert (v1_dir / "deck.pptx").exists()
