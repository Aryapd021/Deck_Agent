import json
from pathlib import Path
import pytest

from app.config import get_settings
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.edit_ops import (
    DeleteSlide,
    EditPlan,
    MoveSlide,
    SetDiagramFocus,
    UpdateSlide,
)
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.services.edit import compact_deck, interpret_command
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


def _make_sample_facts() -> ProjectFacts:
    graph = ComponentGraph(
        nodes=[
            Node(id="frontend", label="Frontend UI", kind=NodeKind.FRONTEND),
            Node(id="backend", label="Backend API", kind=NodeKind.BACKEND),
            Node(id="db", label="Database", kind=NodeKind.DB),
            Node(id="ai", label="LLM Service", kind=NodeKind.AI),
        ],
        edges=[
            Edge(from_="frontend", to="backend"),
            Edge(from_="backend", to="db"),
            Edge(from_="backend", to="ai"),
        ],
    )
    return ProjectFacts(
        name="DeckAgent",
        one_liner=Field(text="Autonomous pitch deck generator", source=Source.REPO, evidence="README.md"),
        problem=[Field(text="Making decks is hard", source=Source.CHAT)],
        solution=[Field(text="Automate deck creation", source=Source.CHAT)],
        features=[Field(text="10 slides generated", source=Source.REPO, evidence="core.py")],
        stack=[Field(text="Python and React", source=Source.REPO, evidence="pyproject.toml")],
        demo_steps=[Field(text="1. Ingest repo", source=Source.REPO, evidence="main.py")],
        impact=[Field(text="Saves 5 hours per hackathon", source=Source.VOICE)],
        roadmap=[Field(text="V2 export to Keynote", source=Source.VOICE)],
        components=graph,
    )


def test_compact_deck_numbering_and_diagram_nodes():
    deck = build_sample_deck("tmpl_test")
    facts = _make_sample_facts()

    compact = compact_deck(deck, facts)

    # 1. Slide count matches
    assert compact["slide_count"] == len(deck.slides)
    assert len(compact["slides"]) == len(deck.slides)

    # 2. Numbering is 1-based
    for idx, s in enumerate(compact["slides"]):
        assert s["n"] == idx + 1
        assert s["id"] == deck.slides[idx].id
        assert s["type"] == deck.slides[idx].type.value
        assert s["title"] == deck.slides[idx].title.text
        assert "notes_chars" in s

        # Bullets are 0-based with i, text, source
        for b_idx, b in enumerate(s["bullets"]):
            assert b["i"] == b_idx
            assert isinstance(b["text"], str)
            assert isinstance(b["source"], str)

    # 3. diagram_nodes from facts.components
    assert len(compact["diagram_nodes"]) == 4
    node_ids = [n["id"] for n in compact["diagram_nodes"]]
    assert node_ids == ["frontend", "backend", "db", "ai"]
    assert compact["diagram_nodes"][0]["label"] == "Frontend UI"


@pytest.mark.asyncio
async def test_interpret_command_shorten_slide(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    facts = _make_sample_facts()
    slide3_id = deck.slides[2].id

    canned_plan = {
        "ops": [
            {
                "op": "update_slide",
                "slide_id": slide3_id,
                "fields": {
                    "bullets": [
                        {"text": "Short bullet 1", "source": "repo", "evidence": "file.py"},
                        {"text": "Short bullet 2", "source": "voice", "evidence": None},
                    ]
                },
            }
        ],
        "message": "Shortened slide 3",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command("Shorten slide 3", deck, facts, None)

    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert isinstance(op, UpdateSlide)
    assert op.op == "update_slide"
    assert op.slide_id == slide33_id if (slide33_id := slide3_id) else True
    assert op.fields.bullets is not None
    assert len(op.fields.bullets) == 2
    assert op.fields.bullets[0].text == "Short bullet 1"
    assert "Shorten slide 3" in fake.calls[0]


@pytest.mark.asyncio
async def test_interpret_command_move_demo_up(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    demo_slide = next(s for s in deck.slides if s.type == SlideType.DEMO)

    canned_plan = {
        "ops": [
            {
                "op": "move_slide",
                "slide_id": demo_slide.id,
                "to_index": 5,
            }
        ],
        "message": "Moved demo up",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command("Move demo up", deck, None, None)

    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert isinstance(op, MoveSlide)
    assert op.slide_id == demo_slide.id
    assert op.to_index == 5


@pytest.mark.asyncio
async def test_interpret_command_delete_slide(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    roadmap_slide = next(s for s in deck.slides if s.type == SlideType.ROADMAP)

    canned_plan = {
        "ops": [
            {
                "op": "delete_slide",
                "slide_id": roadmap_slide.id,
            }
        ],
        "message": "Deleted the roadmap slide",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command("Delete the roadmap slide", deck, None, None)

    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert isinstance(op, DeleteSlide)
    assert op.slide_id == roadmap_slide.id


@pytest.mark.asyncio
async def test_interpret_command_diagram_focus(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    facts = _make_sample_facts()

    canned_plan = {
        "ops": [
            {
                "op": "set_diagram_focus",
                "node_ids": ["frontend", "backend", "db"],
            }
        ],
        "message": "Focused diagram on frontend, backend, and db",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command(
        "Simplify the diagram to frontend, API and database", deck, facts, None
    )

    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert isinstance(op, SetDiagramFocus)
    assert op.node_ids == ["frontend", "backend", "db"]


@pytest.mark.asyncio
async def test_interpret_command_combined(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    facts = _make_sample_facts()
    slide3_id = deck.slides[2].id
    demo_slide = next(s for s in deck.slides if s.type == SlideType.DEMO)

    canned_plan = {
        "ops": [
            {
                "op": "update_slide",
                "slide_id": slide3_id,
                "fields": {
                    "bullets": [
                        {"text": "Shortened bullet", "source": "repo", "evidence": "e.py"}
                    ]
                },
            },
            {
                "op": "move_slide",
                "slide_id": demo_slide.id,
                "to_index": 5,
            },
        ],
        "message": "Shortened slide 3 and moved demo up",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command(
        "Shorten slide 3 and move demo up", deck, facts, None
    )

    assert len(plan.ops) == 2
    assert isinstance(plan.ops[0], UpdateSlide)
    assert isinstance(plan.ops[1], MoveSlide)


@pytest.mark.asyncio
async def test_interpret_command_out_of_scope(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")

    canned_plan = {
        "ops": [],
        "message": "I can't do that yet. Styling and colors are not supported.",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command("make it blue", deck, None, None)

    assert plan.ops == []
    assert plan.message is not None
    assert "can't do that" in plan.message.lower() or "styling" in plan.message.lower()


@pytest.mark.asyncio
async def test_interpret_command_retry_on_invalid_json(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    slide1_id = deck.slides[0].id

    # First attempt: invalid json missing required fields
    invalid_resp = '{"ops": [{"op": "unknown_op"}]}'
    valid_resp = json.dumps({
        "ops": [
            {
                "op": "delete_slide",
                "slide_id": slide1_id,
            }
        ],
        "message": "Deleted slide 1",
    })

    fake = FakeProvider([invalid_resp, valid_resp])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command("delete slide 1", deck, None, None)

    assert len(fake.calls) == 2
    assert len(plan.ops) == 1
    assert isinstance(plan.ops[0], DeleteSlide)
    assert plan.ops[0].slide_id == slide1_id


@pytest.mark.asyncio
async def test_interpret_command_selected_slide(monkeypatch: pytest.MonkeyPatch):
    deck = build_sample_deck("tmpl_test")
    selected_slide = deck.slides[3]

    canned_plan = {
        "ops": [
            {
                "op": "regenerate",
                "slide_id": selected_slide.id,
            }
        ],
        "message": "Regenerated this slide",
    }
    fake = FakeProvider([json.dumps(canned_plan)])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    plan = await interpret_command("regenerate this slide", deck, None, selected_slide.id)

    assert len(plan.ops) == 1
    assert plan.ops[0].op == "regenerate"
    assert plan.ops[0].slide_id == selected_slide.id

    # Verify that the selected slide JSON was in the prompt
    assert selected_slide.id in fake.calls[0]
    assert "<selected>" in fake.calls[0]


@pytest.mark.asyncio
async def test_live_interpret_command_if_key(monkeypatch: pytest.MonkeyPatch):
    from app.core.errors import ApiError

    settings = get_settings()
    has_key = bool(settings.gemini_api_key) or bool(settings.anthropic_api_key)
    if not has_key:
        pytest.skip("No LLM API key present for live test")

    if settings.llm_provider == "gemini":
        monkeypatch.setattr(settings, "gemini_model", "gemini-3.5-flash-lite")
    llm_base.get_provider.cache_clear()

    deck = build_sample_deck("tmpl_test")
    facts = _make_sample_facts()

    try:
        plan = await interpret_command("Shorten slide 3 and move demo up", deck, facts, None)
        print("\n--- Live interpret_command result ---")
        print(f"Plan message: {plan.message}")
        for idx, op in enumerate(plan.ops):
            print(f"Op {idx + 1}: {op.model_dump()}")
        print("-------------------------------------")

        assert isinstance(plan, EditPlan)
        assert len(plan.ops) >= 1
    except ApiError as e:
        pytest.skip(f"Live API check skipped due to provider status: {e.message}")
