import json
from uuid import uuid4
import pytest

from app.core.errors import ApiError, ErrorCode
from app.models.api import FitLimits, SlotMap, TemplateManifest
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.edit_ops import ShortenItem, ShortenResponse, SlidePatch, UpdateSlide
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.services.edit import apply_ops
from app.services.llm import base as llm_base
from app.services.llm.base import LLMProvider
from app.services.validate import enforce_fit
from tests.fixtures.sample_deck import build_sample_deck


class FakeProvider(LLMProvider):
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete_json(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise RuntimeError("FakeProvider ran out of configured responses")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _make_manifest(template_id: str = "tmpl_test") -> TemplateManifest:
    slots: dict[str, SlotMap] = {}
    limits: dict[str, FitLimits] = {}
    for st in SlideType:
        slots[st.value] = SlotMap(
            title_idx=0,
            subtitle_idx=1 if st in (SlideType.TITLE, SlideType.CLOSING) else None,
            body_idx=2 if st not in (SlideType.TITLE, SlideType.CLOSING) else None,
            picture_idx=1 if st in (SlideType.ARCHITECTURE, SlideType.DEMO) else None,
        )
        limits[st.value] = FitLimits(
            title_max=70,
            bullet_max=100,
            bullets_max=5,
            notes_max=600,
        )
    return TemplateManifest(
        template_id=template_id,
        name="Test Template",
        slide_width=9144000,
        slide_height=5143500,
        layouts=[],
        layout_map={st.value: 0 for st in SlideType},
        slots=slots,
        limits=limits,
    )


def _make_facts() -> ProjectFacts:
    graph = ComponentGraph(
        nodes=[
            Node(id="frontend", label="React Frontend", kind=NodeKind.FRONTEND),
            Node(id="backend", label="FastAPI Backend", kind=NodeKind.BACKEND),
            Node(id="db", label="SQLite DB", kind=NodeKind.DB),
            Node(id="ai", label="Gemini LLM", kind=NodeKind.AI),
        ],
        edges=[
            Edge(from_="frontend", to="backend", label="HTTP"),
            Edge(from_="backend", to="db", label="SQL"),
            Edge(from_="backend", to="ai", label="Prompt"),
        ],
    )
    return ProjectFacts(
        name="DeckAgent",
        one_liner=Field(text="Autonomous pitch deck generator", source=Source.REPO, evidence="README.md"),
        problem=[Field(text="Pitch deck creation is slow and manual", source=Source.CHAT)],
        solution=[Field(text="Generate decks from code and chat automatically", source=Source.CHAT)],
        features=[Field(text="Structured facts extraction", source=Source.REPO, evidence="backend/pyproject.toml")],
        stack=[Field(text="Python and TypeScript", source=Source.REPO, evidence="backend/pyproject.toml")],
        demo_steps=[Field(text="Click generate", source=Source.VOICE)],
        impact=[Field(text="Hours saved per deck", source=Source.VOICE)],
        roadmap=[Field(text="Support custom PPTX templates", source=Source.VOICE)],
        components=graph,
    )


@pytest.mark.asyncio
async def test_enforce_fit_no_violations():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=True)
    assert warnings == []
    assert len(result_deck.slides) == len(deck.slides)
    for orig, res in zip(deck.slides, result_deck.slides):
        assert orig.title.text == res.title.text


@pytest.mark.asyncio
async def test_enforce_fit_llm_valid_shorter_text(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide1 = deck.slides[0]

    # Make slide 1 title violate limit (70 chars)
    long_title = "This is a very long title for the presentation that clearly exceeds seventy characters limit"
    slide1.title = Field(text=long_title, source=Source.REPO, evidence="README.md")

    short_response = ShortenResponse(
        items=[ShortenItem(slide_id=slide1.id, field="title", text="Concise Presentation Title")]
    )
    fake = FakeProvider([short_response.model_dump_json()])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=True)

    assert len(fake.calls) == 1
    assert warnings == []  # No warnings because LLM shortened successfully
    after_slide1 = result_deck.slides[0]
    assert after_slide1.title.text == "Concise Presentation Title"
    assert after_slide1.title.source == Source.REPO
    assert after_slide1.title.evidence == "README.md"


@pytest.mark.asyncio
async def test_enforce_fit_llm_too_long_text_falls_back_to_word_boundary(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide1 = deck.slides[0]

    long_title = "This is a very long title for the presentation that clearly exceeds seventy characters limit"
    slide1.title = Field(text=long_title, source=Source.USER)

    # LLM returns text that still exceeds the 70 char limit
    still_too_long = "This is still a ridiculously lengthy shortened title candidate that will certainly not fit into seventy chars at all"
    short_response = ShortenResponse(
        items=[ShortenItem(slide_id=slide1.id, field="title", text=still_too_long)]
    )
    fake = FakeProvider([short_response.model_dump_json()])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=True)

    assert len(fake.calls) == 1
    assert warnings == ["Slide 1 title truncated"]
    after_title = result_deck.slides[0].title.text
    assert len(after_title) <= 70
    assert after_title.endswith("...")
    # Word boundary check: truncated before a full word
    assert not after_title[:-3].endswith(" ")


@pytest.mark.asyncio
async def test_enforce_fit_llm_invalid_output_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide1 = deck.slides[0]

    long_title = "A comprehensive analysis of enterprise software architecture trends and operational benchmarks"
    slide1.title = Field(text=long_title, source=Source.USER)

    # Fake provider raises ApiError(ErrorCode.LLM_INVALID_OUTPUT)
    fake = FakeProvider([ApiError(ErrorCode.LLM_INVALID_OUTPUT, "Failed twice")])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=True)

    assert len(fake.calls) == 1
    assert warnings == ["Slide 1 title truncated"]
    after_title = result_deck.slides[0].title.text
    assert len(after_title) <= 70
    assert after_title.endswith("...")


@pytest.mark.asyncio
async def test_enforce_fit_seven_bullets_trimmed():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide2 = deck.slides[1]

    # Set 7 bullets (each within the 100 char limit)
    slide2.bullets = [
        Field(text=f"Bullet item number {i}", source=Source.USER)
        for i in range(1, 8)
    ]
    assert len(slide2.bullets) == 7

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=False)

    after_slide2 = result_deck.slides[1]
    assert len(after_slide2.bullets) == 5
    assert [b.text for b in after_slide2.bullets] == [f"Bullet item number {i}" for i in range(1, 6)]
    assert warnings == ["Slide 2 bullets truncated"]


@pytest.mark.asyncio
async def test_enforce_fit_bullets_max_zero_drops_all():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide1 = deck.slides[0]  # Title slide

    manifest.limits[slide1.type.value].bullets_max = 0
    slide1.bullets = [
        Field(text="Stray bullet 1", source=Source.USER),
        Field(text="Stray bullet 2", source=Source.USER),
    ]

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=False)

    after_slide1 = result_deck.slides[0]
    assert len(after_slide1.bullets) == 0
    assert warnings == ["Slide 1 bullets truncated"]


@pytest.mark.asyncio
async def test_enforce_fit_use_llm_false_never_calls_provider(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide1 = deck.slides[0]

    long_title = "A super long presentation title that requires shortening to fit the slide limits strictly"
    slide1.title = Field(text=long_title, source=Source.USER)

    def _should_not_be_called():
        raise AssertionError("Provider must not be called when use_llm is False")

    monkeypatch.setattr(llm_base, "get_provider", _should_not_be_called)

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=False)

    assert warnings == ["Slide 1 title truncated"]
    assert len(result_deck.slides[0].title.text) <= 70
    assert result_deck.slides[0].title.text.endswith("...")


@pytest.mark.asyncio
async def test_enforce_fit_bullet_and_notes_truncation():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    slide3 = deck.slides[2]

    # Bullet 2 (index 1) exceeds 100 chars
    long_bullet = "This is bullet two and it contains an excessive amount of verbose commentary explaining edge cases in great detail beyond hundred characters"
    slide3.bullets[1] = Field(text=long_bullet, source=Source.USER)

    # Notes exceeds 600 chars
    slide3.notes = "Here are notes about slide 3. " * 30
    assert len(slide3.notes) > 600

    result_deck, warnings = await enforce_fit(deck, manifest, use_llm=False)

    after_slide3 = result_deck.slides[2]
    assert len(after_slide3.bullets[1].text) <= 100
    assert after_slide3.bullets[1].text.endswith("...")
    assert len(after_slide3.notes) <= 600
    assert after_slide3.notes.endswith("...")

    assert "Slide 3 bullet 2 truncated" in warnings
    assert "Slide 3 notes truncated" in warnings


@pytest.mark.asyncio
async def test_apply_ops_auto_shortens_overflow_instead_of_rejecting(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()
    slide3 = deck.slides[2]

    long_bullet = "An excessively verbose updated bullet description that extends well beyond the maximum limit of one hundred characters allowed"
    op = UpdateSlide(
        slide_id=slide3.id,
        fields=SlidePatch(bullets=[Field(text=long_bullet, source=Source.USER)]),
    )

    # Provider returns still-too-long text so deterministic truncation triggers
    fake = FakeProvider([
        ShortenResponse(
            items=[ShortenItem(slide_id=slide3.id, field="bullet:0", text="X" * 120)]
        ).model_dump_json()
    ])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    res = await apply_ops(deck, [op], facts, manifest)
    new_deck, summary = res[0], res[1]
    warnings = getattr(res, "warnings", [])

    assert len(new_deck.slides[2].bullets) == 1
    assert len(new_deck.slides[2].bullets[0].text) <= 100
    assert new_deck.slides[2].bullets[0].text.endswith("...")
    assert "Slide 3 bullet 1 truncated" in warnings


@pytest.mark.asyncio
async def test_run_edit_appends_truncated_summary_and_emits_events(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from pathlib import Path
    from datetime import datetime, timezone
    from pptx import Presentation
    from sqlmodel import select
    from app.config import get_settings
    from app.core.jobs import JobContext, run_edit
    from app.core.storage import version_dir
    from app.db import init_db, session_scope
    from app.models.api import EditRequest
    from app.models.edit_ops import EditPlan
    from app.models.tables import DeckVersionRow, JobRow, ProjectRow, TemplateRow
    from app.services.template_inspector import inspect_template

    test_storage = tmp_path / "storage"
    test_templates = tmp_path / "templates"
    test_templates.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)
    monkeypatch.setattr(type(get_settings()), "templates_dir", property(lambda self: test_templates))

    import app.db as db_mod
    db_mod._engine = None
    init_db()

    project_id = "proj_fit_edit"
    template_id = "tmpl_fit"
    template_path = test_templates / f"{template_id}.pptx"
    prs = Presentation()
    prs.save(str(template_path))
    manifest = inspect_template(template_path, template_id, "Fit Template")
    manifest.limits = {
        st.value: FitLimits(title_max=70, bullet_max=100, bullets_max=5, notes_max=600)
        for st in SlideType
    }

    deck = build_sample_deck(template_id, project_id)
    deck.version = 1
    facts = _make_facts()

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        tmpl_row = TemplateRow(
            id=template_id,
            name="Fit Template",
            path=f"templates/{template_id}.pptx",
            manifest_json=manifest.model_dump_json(),
            created_at=now,
        )
        session.add(tmpl_row)
        session.flush()

        proj = ProjectRow(
            id=project_id,
            name="Fit Project",
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
            summary="Initial",
            created_at=now,
        )
        session.add(v1_row)

        job_row = JobRow(
            id="job_fit_1",
            project_id=project_id,
            type="edit",
            status="running",
            stage=None,
            error_code=None,
            started_at=now,
            ended_at=None,
        )
        session.add(job_row)


    VALID_PNG_RED = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\n\x00\x00\x00\n\x08\x02\x00\x00\x00"
        b"\x02PX\xea\x00\x00\x00\x13IDATx\x9cc\xfc\xcf\x80\x0f0\xe1\x95e\x18\xa9\xd2\x00A,\x01\x13y\xed\xba&\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    v1_dir = version_dir(project_id, 1)
    v1_dir.mkdir(parents=True, exist_ok=True)
    (v1_dir / "deck.json").write_text(deck.model_dump_json(), encoding="utf-8")
    (v1_dir / "facts.json").write_text(facts.model_dump_json(), encoding="utf-8")
    (v1_dir / "deck.pptx").write_bytes(b"PPTX")
    (v1_dir / "diagram.png").write_bytes(VALID_PNG_RED)


    slide3 = deck.slides[2]
    long_bullet = "A very long bullet that exceeds one hundred characters by an enormous margin so it definitely violates layout limits"
    plan = EditPlan(
        ops=[
            UpdateSlide(
                op="update_slide",
                slide_id=slide3.id,
                fields=SlidePatch(bullets=[Field(text=long_bullet, source=Source.USER)]),
            )
        ]
    )

    # Provider returns plan first (for interpret), then for shorten returns still-too-long
    fake = FakeProvider([
        plan.model_dump_json(),
        ShortenResponse(
            items=[ShortenItem(slide_id=slide3.id, field="bullet:0", text="Y" * 150)]
        ).model_dump_json(),
    ])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    ctx = JobContext(job_id="job_fit_1", project_id=project_id)
    emitted_events = []
    orig_emit = ctx.emit

    async def mock_emit(stage, status, message, version=None):
        emitted_events.append({"stage": stage, "status": status, "message": message})
        await orig_emit(stage, status, message, version)

    ctx.emit = mock_emit  # type: ignore

    res = await run_edit(ctx, EditRequest(command="Update slide 3 with long text", base_version=1))

    assert res.version == 2
    assert "(1 fields truncated)" in res.message

    # Check progress events for truncation warning
    truncation_progress_events = [
        e for e in emitted_events
        if e["stage"] == "apply" and e["status"] == "progress" and "truncated" in e["message"]
    ]
    assert len(truncation_progress_events) == 1
    assert "Slide 3 bullet 1 truncated" in truncation_progress_events[0]["message"]


