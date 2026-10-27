import json
from uuid import uuid4
import pytest

from app.config import Settings
from app.models.api import FitLimits, SlotMap, TemplateManifest
from app.models.deck import Deck, DeckDraft, Field, Slide, SlideType, Source
from app.models.facts import ComponentGraph, Edge, Node, NodeKind, ProjectFacts
from app.services.extract import build_deck, manifest_capabilities
from app.services.llm import base as llm_base
from app.services.llm.base import LLMProvider, load_prompt
from app.services.validate import find_fit_violations, validate_deck
from tests.fixtures.sample_deck import build_sample_deck


class FakeProvider(LLMProvider):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    async def complete_json(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


def _make_dummy_manifest(template_id: str = "tmpl_test") -> TemplateManifest:
    slots = {}
    limits = {}
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
        name="TestProject",
        one_liner=Field(text="A test project for autonomous pitch decks", source=Source.REPO, evidence="README.md"),
        problem=[Field(text="Manual decks are slow", source=Source.CHAT)],
        solution=[Field(text="Automated slide generation", source=Source.VOICE)],
        features=[Field(text="Fast rendering", source=Source.REPO, evidence="render.py")],
        stack=[Field(text="Python and FastAPI", source=Source.REPO, evidence="pyproject.toml")],
        demo_steps=[Field(text="Click generate button", source=Source.VOICE)],
        impact=[Field(text="TODO: Add user metrics", source=Source.NEEDS_INPUT)],
        roadmap=[Field(text="TODO: Add export to PDF", source=Source.NEEDS_INPUT)],
        components=graph,
    )


def test_load_prompts_no_leftover_tokens() -> None:
    facts_prompt = load_prompt(
        "facts",
        repo_digest="{}",
        pitch_notes="test pitch",
        chat_digest="{}",
        criteria="test criteria",
    )
    assert "<repo_digest>" in facts_prompt
    assert "test pitch" in facts_prompt
    assert "{{" not in facts_prompt and "}}" not in facts_prompt

    deck_prompt = load_prompt(
        "deck",
        facts="{}",
        capabilities="{}",
        max_slides="10",
    )
    assert "<facts>" in deck_prompt
    assert "max_slides" in deck_prompt
    assert "{{" not in deck_prompt and "}}" not in deck_prompt


@pytest.mark.asyncio
async def test_build_deck_canonical_order_ids_diagram(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _make_dummy_manifest()
    facts = _make_dummy_facts()

    # Create 10 sample slides and shuffle them into reverse order
    sample = build_sample_deck(template_id=manifest.template_id)
    shuffled_slides = list(reversed(sample.slides))
    orig_ids = [s.id for s in shuffled_slides]

    fake = FakeProvider(DeckDraft(slides=shuffled_slides).model_dump_json())
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    deck = await build_deck(facts, manifest, project_id="proj_1", version=1)

    # 1. Canonical order enforced
    expected_order = [
        SlideType.TITLE,
        SlideType.PROBLEM,
        SlideType.SOLUTION,
        SlideType.FEATURES,
        SlideType.ARCHITECTURE,
        SlideType.TECH_STACK,
        SlideType.DEMO,
        SlideType.IMPACT,
        SlideType.ROADMAP,
        SlideType.CLOSING,
    ]
    assert [s.type for s in deck.slides] == expected_order

    # 2. Fresh UUIDs assigned
    new_ids = [s.id for s in deck.slides]
    assert len(set(new_ids)) == len(new_ids)
    for nid in new_ids:
        assert nid not in orig_ids

    # 3. Diagram set
    assert deck.diagram == facts.components

    # 4. Architecture image_ref is "diagram", others None
    for s in deck.slides:
        if s.type == SlideType.ARCHITECTURE:
            assert s.image_ref == "diagram"
        else:
            assert s.image_ref is None


@pytest.mark.asyncio
async def test_build_deck_max_slides_8_drops_roadmap_impact(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _make_dummy_manifest()
    facts = _make_dummy_facts()
    sample = build_sample_deck(template_id=manifest.template_id)

    fake = FakeProvider(DeckDraft(slides=sample.slides).model_dump_json())
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)
    monkeypatch.setattr(
        "app.services.extract.get_settings",
        lambda: Settings(max_slides=8),
    )

    deck = await build_deck(facts, manifest, project_id="proj_8", version=1)

    assert len(deck.slides) == 8
    types_present = [s.type for s in deck.slides]
    assert SlideType.ROADMAP not in types_present
    assert SlideType.IMPACT not in types_present
    assert SlideType.TECH_STACK in types_present


def test_validate_deck_provenance() -> None:
    manifest = _make_dummy_manifest()
    known_paths = {"src/main.py", "README.md"}

    deck = Deck(
        schema_version=1,
        project_id="test_p",
        version=1,
        template_id=manifest.template_id,
        slides=[
            # 1. Title slide: repo source but evidence NOT in known_paths -> demoted to needs_input
            Slide(
                type=SlideType.TITLE,
                title=Field(text="Bad Evidence Title", source=Source.REPO, evidence="missing_file.py"),
                subtitle=Field(text="Valid Voice Subtitle", source=Source.VOICE),
            ),
            # 2. Problem slide: chat source -> ALLOWED on problem slide
            Slide(
                type=SlideType.PROBLEM,
                title=Field(text="Problem Title", source=Source.CHAT),
                bullets=[
                    Field(text="Problem bullet from chat", source=Source.CHAT),
                ],
            ),
            # 3. Features slide: chat source -> DISALLOWED on features slide -> demoted to needs_input
            Slide(
                type=SlideType.FEATURES,
                title=Field(text="Features Title", source=Source.REPO, evidence="README.md"),
                bullets=[
                    Field(text="Feature claimed in chat", source=Source.CHAT),
                ],
            ),
            # 4. Architecture slide: valid repo evidence + needs_input with stray evidence
            Slide(
                type=SlideType.ARCHITECTURE,
                title=Field(text="Arch Title", source=Source.REPO, evidence="src/main.py"),
                bullets=[
                    Field(text="Needs input bullet", source=Source.NEEDS_INPUT, evidence="stray_evidence.py"),
                ],
            ),
        ],
    )

    report = validate_deck(deck, manifest, known_paths=known_paths)
    corrected = report.deck

    # Slide 1 check: demoted to needs_input with "TODO: verify " prefix, evidence cleared
    s1 = corrected.slides[0]
    assert s1.title.source == Source.NEEDS_INPUT
    assert s1.title.text.startswith("TODO: verify Bad Evidence Title")
    assert s1.title.evidence is None
    assert s1.subtitle.source == Source.VOICE

    # Slide 2 check: problem slide allows CHAT
    s2 = corrected.slides[1]
    assert s2.title.source == Source.CHAT
    assert s2.bullets[0].source == Source.CHAT

    # Slide 3 check: features slide demotes CHAT to needs_input with "TODO: " prefix
    s3 = corrected.slides[2]
    assert s3.title.source == Source.REPO
    assert s3.bullets[0].source == Source.NEEDS_INPUT
    assert s3.bullets[0].text.startswith("TODO: Feature claimed in chat")
    assert s3.bullets[0].evidence is None

    # Slide 4 check: valid repo kept, needs_input evidence stripped
    s4 = corrected.slides[3]
    assert s4.title.source == Source.REPO
    assert s4.title.evidence == "src/main.py"
    assert s4.bullets[0].source == Source.NEEDS_INPUT
    assert s4.bullets[0].evidence is None

    # Warnings recorded
    assert len(report.warnings) >= 3


def test_find_fit_violations_over_limit_bullet() -> None:
    manifest = _make_dummy_manifest()
    # Problem slide bullet_max is 100
    manifest.limits[SlideType.PROBLEM.value] = FitLimits(bullet_max=100)

    over_limit_text = "X" * 150
    deck = Deck(
        schema_version=1,
        project_id="test_fit",
        version=1,
        template_id=manifest.template_id,
        slides=[
            Slide(
                id="slide_prob",
                type=SlideType.PROBLEM,
                title=Field(text="Valid Title", source=Source.VOICE),
                bullets=[
                    Field(text=over_limit_text, source=Source.VOICE),
                ],
            )
        ],
    )

    violations = find_fit_violations(deck, manifest)
    assert len(violations) == 1
    v = violations[0]
    assert v.slide_id == "slide_prob"
    assert v.field == "bullet:0"
    assert v.limit == 100
    assert v.actual == 150
