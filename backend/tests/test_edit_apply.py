import json
from uuid import uuid4
import pytest

from app.models.api import FitLimits, SlotMap, TemplateManifest
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.edit_ops import (
    AddSlide,
    DeleteSlide,
    MoveSlide,
    Regenerate,
    SetDiagramFocus,
    SlidePatch,
    UpdateSlide,
)
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.services.edit import EditRejected, apply_ops, summarize_ops
from app.services.llm import base as llm_base
from app.services.llm.base import LLMProvider
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
            Node(id="worker", label="Worker Process", kind=NodeKind.INFRA),
        ],
        edges=[
            Edge(from_="frontend", to="backend", label="HTTP"),
            Edge(from_="backend", to="db", label="SQL"),
            Edge(from_="backend", to="ai", label="Prompt"),
            Edge(from_="backend", to="worker", label="Queue"),
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
async def test_apply_update_slide():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()
    slide3 = deck.slides[2]
    orig_bullets_count = len(slide3.bullets)
    assert orig_bullets_count > 1

    new_bullets = [
        Field(text="Shortened bullet 1", source=Source.REPO, evidence="backend/pyproject.toml"),
        Field(text="Shortened bullet 2", source=Source.VOICE, evidence=None),
    ]
    op = UpdateSlide(
        slide_id=slide3.id,
        fields=SlidePatch(bullets=new_bullets),
    )

    new_deck, summary = await apply_ops(deck, [op], facts, manifest)

    # Before assertions
    assert len(deck.slides[2].bullets) == orig_bullets_count
    assert deck.version == 1

    # After assertions
    assert new_deck.version == 2
    after_slide3 = new_deck.slides[2]
    assert after_slide3.id == slide3.id
    assert after_slide3.type == slide3.type
    assert after_slide3.title.text == slide3.title.text
    assert len(after_slide3.bullets) == 2
    assert after_slide3.bullets[0].text == "Shortened bullet 1"
    assert f"Slide 3: {orig_bullets_count} bullets to 2" in summary


@pytest.mark.asyncio
async def test_apply_move_slide_and_undo_friendliness():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()

    slide0_id = deck.slides[0].id
    slide1_id = deck.slides[1].id
    all_before_ids = [s.id for s in deck.slides]

    op = MoveSlide(slide_id=slide0_id, to_index=1)
    new_deck, summary = await apply_ops(deck, [op], facts, manifest)

    # Before unchanged
    assert deck.slides[0].id == slide0_id
    assert deck.slides[1].id == slide1_id

    # After moved
    assert new_deck.slides[0].id == slide1_id
    assert new_deck.slides[1].id == slide0_id

    # Undo-friendliness: all slide ids survive the move
    all_after_ids = [s.id for s in new_deck.slides]
    assert set(all_after_ids) == set(all_before_ids)
    assert len(all_after_ids) == len(all_before_ids)
    assert "moved" in summary
    assert "to position 2" in summary


@pytest.mark.asyncio
async def test_apply_delete_slide():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()
    slide_to_delete = deck.slides[1]

    op = DeleteSlide(slide_id=slide_to_delete.id)
    new_deck, summary = await apply_ops(deck, [op], facts, manifest)

    assert len(deck.slides) == 10
    assert len(new_deck.slides) == 9
    assert slide_to_delete.id not in [s.id for s in new_deck.slides]
    assert f"deleted slide 2 ({slide_to_delete.title.text})" in summary


@pytest.mark.asyncio
async def test_apply_delete_slide_refused_under_two():
    deck = build_sample_deck("tmpl_test")
    deck.slides = deck.slides[:2]
    manifest = _make_manifest()
    facts = _make_facts()

    op = DeleteSlide(slide_id=deck.slides[0].id)
    with pytest.raises(EditRejected) as exc_info:
        await apply_ops(deck, [op], facts, manifest)
    assert "fewer than 2 slides" in str(exc_info.value).lower()
    assert len(deck.slides) == 2


@pytest.mark.asyncio
async def test_apply_add_slide():
    deck = build_sample_deck("tmpl_test")
    deck.slides = deck.slides[:5]
    manifest = _make_manifest()
    facts = _make_facts()
    target_after = deck.slides[1]

    op = AddSlide(
        after_slide_id=target_after.id,
        type=SlideType.PROBLEM,
        content=SlidePatch(
            title=Field(text="Additional Problem", source=Source.USER),
            bullets=[Field(text="New issue identified", source=Source.USER)],
        ),
    )

    new_deck, summary = await apply_ops(deck, [op], facts, manifest)

    assert len(deck.slides) == 5
    assert len(new_deck.slides) == 6
    added_slide = new_deck.slides[2]
    assert added_slide.title.text == "Additional Problem"
    assert added_slide.type == SlideType.PROBLEM
    assert added_slide.image_ref is None
    assert len(added_slide.bullets) == 1
    assert "added problem slide at position 3" in summary


@pytest.mark.asyncio
async def test_apply_add_architecture_slide_sets_image_ref():
    deck = build_sample_deck("tmpl_test")
    deck.slides = deck.slides[:4]
    manifest = _make_manifest()
    facts = _make_facts()

    op = AddSlide(
        after_slide_id=deck.slides[0].id,
        type=SlideType.ARCHITECTURE,
        content=SlidePatch(
            title=Field(text="System Architecture", source=Source.REPO, evidence="README.md"),
        ),
    )

    new_deck, _ = await apply_ops(deck, [op], facts, manifest)
    added = new_deck.slides[1]
    assert added.type == SlideType.ARCHITECTURE
    assert added.image_ref == "diagram"


@pytest.mark.asyncio
async def test_apply_set_diagram_focus():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()

    # 1. Focus on 4 components
    op_focus = SetDiagramFocus(node_ids=["frontend", "backend", "db", "ai"])
    new_deck, summary = await apply_ops(deck, [op_focus], facts, manifest)

    assert new_deck.diagram is not None
    assert len(new_deck.diagram.nodes) == 4
    kept_ids = {n.id for n in new_deck.diagram.nodes}
    assert kept_ids == {"frontend", "backend", "db", "ai"}
    # Only edges between kept nodes survive
    for e in new_deck.diagram.edges:
        assert e.from_ in kept_ids and e.to in kept_ids
    assert "diagram focus set to 4 components" in summary

    # 2. Focus on 2 components is rejected (< 4)
    op_invalid = SetDiagramFocus(node_ids=["frontend", "backend"])
    with pytest.raises(EditRejected) as exc_info:
        await apply_ops(deck, [op_invalid], facts, manifest)
    assert "The diagram needs at least 4 components" in str(exc_info.value)

    # 3. Empty list restores full diagram
    op_restore = SetDiagramFocus(node_ids=[])
    restored_deck, summary_restored = await apply_ops(new_deck, [op_restore], facts, manifest)
    assert len(restored_deck.diagram.nodes) == 5
    assert "diagram restored" in summary_restored


@pytest.mark.asyncio
async def test_apply_regenerate_slide(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()
    slide2 = deck.slides[1]

    canned_slide = Slide(
        id="temp_id",
        type=SlideType.PROBLEM,
        title=Field(text="Regenerated Problem Title", source=Source.CHAT),
        subtitle=None,
        bullets=[
            Field(text="Regenerated problem bullet 1", source=Source.CHAT),
            Field(text="Regenerated problem bullet 2", source=Source.CHAT),
        ],
        image_ref=None,
        notes="Regenerated notes",
    )
    fake = FakeProvider([canned_slide.model_dump_json()])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    op = Regenerate(slide_id=slide2.id)
    new_deck, summary = await apply_ops(deck, [op], facts, manifest)

    after_slide2 = new_deck.slides[1]
    # Original id and type are preserved
    assert after_slide2.id == slide2.id
    assert after_slide2.type == slide2.type
    assert after_slide2.title.text == "Regenerated Problem Title"
    assert len(after_slide2.bullets) == 2
    assert "regenerated slide 2" in summary


@pytest.mark.asyncio
async def test_atomicity_unknown_slide_id():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()

    op = UpdateSlide(
        slide_id="nonexistent_id",
        fields=SlidePatch(title=Field(text="X", source=Source.USER)),
    )

    with pytest.raises(EditRejected) as exc_info:
        await apply_ops(deck, [op], facts, manifest)

    assert "not found" in str(exc_info.value).lower()
    # Input deck is completely unchanged
    assert deck.version == 1
    assert len(deck.slides) == 10


@pytest.mark.asyncio
async def test_atomicity_two_op_batch_second_invalid_leaves_nothing_applied():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()

    slide0_id = deck.slides[0].id
    slide1_id = deck.slides[1].id

    # Op 1 is valid move, Op 2 has unknown slide_id
    op1 = MoveSlide(slide_id=slide0_id, to_index=1)
    op2 = DeleteSlide(slide_id="unknown_slide_id")

    with pytest.raises(EditRejected):
        await apply_ops(deck, [op1, op2], facts, manifest)

    # NOTHING is applied: slide0 is still at index 0
    assert deck.slides[0].id == slide0_id
    assert deck.slides[1].id == slide1_id
    assert deck.version == 1


@pytest.mark.asyncio
async def test_to_index_out_of_range():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()
    slide_id = deck.slides[0].id

    # Negative index
    with pytest.raises(EditRejected) as exc1:
        await apply_ops(deck, [MoveSlide(slide_id=slide_id, to_index=-1)], facts, manifest)
    assert "out of range" in str(exc1.value).lower()

    # Beyond upper bound
    with pytest.raises(EditRejected) as exc2:
        await apply_ops(deck, [MoveSlide(slide_id=slide_id, to_index=100)], facts, manifest)
    assert "out of range" in str(exc2.value).lower()


@pytest.mark.asyncio
async def test_add_beyond_max_slides(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()
    assert len(deck.slides) == 10

    op = AddSlide(
        after_slide_id=deck.slides[0].id,
        type=SlideType.PROBLEM,
        content=SlidePatch(title=Field(text="Too many slides", source=Source.USER)),
    )

    with pytest.raises(EditRejected) as exc_info:
        await apply_ops(deck, [op], facts, manifest)

    assert "maximum" in str(exc_info.value).lower() or "max_slides" in str(exc_info.value).lower()
    assert len(deck.slides) == 10


@pytest.mark.asyncio
async def test_version_increments_by_exactly_one():
    deck = build_sample_deck("tmpl_test")
    deck.version = 5
    manifest = _make_manifest()
    facts = _make_facts()

    op = MoveSlide(slide_id=deck.slides[0].id, to_index=1)
    new_deck, _ = await apply_ops(deck, [op], facts, manifest)

    assert new_deck.version == 6


def test_summarize_ops_compound_formatting():
    deck = build_sample_deck("tmpl_test")
    slide3 = deck.slides[2]
    demo_slide = next(s for s in deck.slides if s.type == SlideType.DEMO)
    demo_slide.title.text = "Demo"

    op1 = UpdateSlide(
        slide_id=slide3.id,
        fields=SlidePatch(bullets=[
            Field(text="b1", source=Source.USER),
            Field(text="b2", source=Source.USER),
            Field(text="b3", source=Source.USER),
        ]),
    )
    op2 = MoveSlide(slide_id=demo_slide.id, to_index=5)

    after_deck = deck.model_copy(deep=True)
    after_slide3 = after_deck.slides[2]
    after_slide3.bullets = list(op1.fields.bullets)
    demo_pop = after_deck.slides.pop(6)
    after_deck.slides.insert(5, demo_pop)

    summary = summarize_ops(deck, after_deck, [op1, op2])
    assert summary == f"Slide 3: {len(slide3.bullets)} bullets to 3; moved Demo to position 6"


@pytest.mark.asyncio
async def test_apply_layout_style():
    deck = build_sample_deck("tmpl_test")
    manifest = _make_manifest()
    facts = _make_facts()

    slide = deck.slides[2]
    op = UpdateSlide(
        slide_id=slide.id,
        fields=SlidePatch(layout_style="vertical"),
    )
    new_deck, _ = await apply_ops(deck, [op], facts, manifest)
    target = next(s for s in new_deck.slides if s.id == slide.id)
    assert target.layout_style == "vertical"

    summary = summarize_ops(deck, new_deck, [op])
    assert "layout changed to vertical" in summary
