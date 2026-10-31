import json
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.models.api import TemplateManifest
from app.models.deck import Deck, Slide, SlideType
from app.models.edit_ops import (
    AddSlide,
    DeleteSlide,
    EditOp,
    EditPlan,
    MoveSlide,
    Regenerate,
    SetDiagramFocus,
    UpdateSlide,
)
from app.models.facts import ComponentGraph, ProjectFacts
from app.services.extract import manifest_capabilities
from app.services.llm.base import generate_structured, load_prompt
from app.services.validate import (
    _check_field,
    check_op,
    enforce_fit,
    find_fit_violations,
    validate_deck,
)


class ApplyResult(tuple):
    deck: Deck
    summary: str
    warnings: list[str]

    def __new__(cls, deck: Deck, summary: str, warnings: list[str] | None = None):
        inst = super().__new__(cls, (deck, summary))
        inst.deck = deck
        inst.summary = summary
        inst.warnings = warnings if warnings is not None else []
        return inst

    def __getitem__(self, index: Any) -> Any:
        if index == 2:
            return self.warnings
        return super().__getitem__(index)


class EditRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message



def compact_deck(deck: Deck, facts: ProjectFacts | None = None) -> dict[str, Any]:
    """Builds a compact summary of a deck and its facts for LLM edit prompts.

    slides: list of {n (1-based), id, type, title (text), bullets: [{i (0-based), text, source}], notes_chars}
    diagram_nodes: list of {id, label} from facts.components (the full graph)
    slide_count: total number of slides
    """
    slides = []
    for idx, slide in enumerate(deck.slides):
        title_text = slide.title.text if slide.title else ""
        bullets = [
            {
                "i": b_idx,
                "text": b.text,
                "source": b.source.value if hasattr(b.source, "value") else str(b.source),
            }
            for b_idx, b in enumerate(slide.bullets)
        ]
        slides.append({
            "n": idx + 1,
            "id": slide.id,
            "type": slide.type.value if hasattr(slide.type, "value") else str(slide.type),
            "title": title_text,
            "bullets": bullets,
            "notes_chars": len(slide.notes or ""),
            "layout_style": getattr(slide, "layout_style", None),
        })

    diagram_nodes = []
    if facts and facts.components and facts.components.nodes:
        diagram_nodes = [{"id": node.id, "label": node.label} for node in facts.components.nodes]
    elif deck.diagram and deck.diagram.nodes:
        diagram_nodes = [{"id": node.id, "label": node.label} for node in deck.diagram.nodes]

    return {
        "slides": slides,
        "diagram_nodes": diagram_nodes,
        "slide_count": len(deck.slides),
    }


async def interpret_command(
    command: str,
    deck: Deck,
    facts: ProjectFacts | None = None,
    target_slide_id: str | None = None,
) -> EditPlan:
    """Interprets a natural-language edit command into a structured EditPlan."""
    compact = compact_deck(deck, facts)

    selected = "none"
    if target_slide_id:
        for s in compact["slides"]:
            if s["id"] == target_slide_id:
                selected = json.dumps(s, indent=2)
                break

    deck_json = json.dumps(compact, indent=2)
    prompt = load_prompt(
        "edit_interpret",
        command=command,
        deck=deck_json,
        selected=selected,
    )

    plan = await generate_structured(prompt, EditPlan)
    return plan


def summarize_ops(before: Deck, after: Deck, ops: list[EditOp]) -> str:
    """Summarizes a list of applied EditOps into a human-readable string joined by '; '."""
    parts: list[str] = []
    before_map = {s.id: (i, s) for i, s in enumerate(before.slides)}

    for op in ops:
        if isinstance(op, UpdateSlide):
            idx, s = before_map.get(op.slide_id, (0, None))
            n = idx + 1
            if op.fields.bullets is not None and s is not None:
                a = len(s.bullets)
                b = len(op.fields.bullets)
                parts.append(f"Slide {n}: {a} bullets to {b}")
            elif op.fields.title is not None:
                parts.append(f"Slide {n}: title reworded")
            elif op.fields.notes is not None:
                parts.append(f"Slide {n}: notes updated")
            elif op.fields.subtitle is not None:
                parts.append(f"Slide {n}: subtitle updated")
            elif op.fields.layout_style is not None:
                parts.append(f"Slide {n}: layout changed to {op.fields.layout_style}")
            else:
                parts.append(f"Slide {n}: updated")

        elif isinstance(op, MoveSlide):
            _, s = before_map.get(op.slide_id, (0, None))
            title = s.title.text if s and s.title and s.title.text else (s.type.value.capitalize() if s else "Slide")
            k = op.to_index + 1
            parts.append(f"moved {title} to position {k}")

        elif isinstance(op, DeleteSlide):
            idx, s = before_map.get(op.slide_id, (0, None))
            n = idx + 1
            title = s.title.text if s and s.title and s.title.text else (s.type.value if s else "")
            parts.append(f"deleted slide {n} ({title})")

        elif isinstance(op, AddSlide):
            # 1-based position in after deck
            after_idx = next(
                (
                    i
                    for i, s in enumerate(after.slides)
                    if s.type == op.type and s.title and s.title.text == op.content.title.text
                ),
                None,
            )
            if after_idx is not None:
                k = after_idx + 1
            else:
                before_idx, _ = before_map.get(op.after_slide_id, (0, None))
                k = before_idx + 2
            type_str = op.type.value if hasattr(op.type, "value") else str(op.type)
            parts.append(f"added {type_str} slide at position {k}")

        elif isinstance(op, SetDiagramFocus):
            if op.node_ids:
                parts.append(f"diagram focus set to {len(op.node_ids)} components")
            else:
                parts.append("diagram restored")

        elif isinstance(op, Regenerate):
            idx, _ = before_map.get(op.slide_id, (0, None))
            n = idx + 1
            parts.append(f"regenerated slide {n}")

    return "; ".join(parts)


async def apply_ops(
    deck: Deck,
    ops: list[EditOp],
    facts: ProjectFacts | None,
    manifest: TemplateManifest,
) -> tuple[Deck, str]:
    """Applies a sequence of EditOps atomically to a deck, returning (new_deck, summary)."""
    working_deck = deck.model_copy(deep=True)
    settings = get_settings()
    max_slides = settings.max_slides

    for op in ops:
        # Check against CURRENT working deck state
        err = check_op(working_deck, op, facts, max_slides)
        if err:
            raise EditRejected(err)

        if isinstance(op, UpdateSlide):
            slide = next(s for s in working_deck.slides if s.id == op.slide_id)
            if op.fields.title is not None:
                slide.title = op.fields.title
            if op.fields.subtitle is not None:
                slide.subtitle = op.fields.subtitle
            if getattr(op.fields, "summary", None) is not None:
                slide.summary = op.fields.summary
            if op.fields.bullets is not None:
                slide.bullets = list(op.fields.bullets)
            if getattr(op.fields, "takeaway", None) is not None:
                slide.takeaway = op.fields.takeaway
            if op.fields.notes is not None:
                slide.notes = op.fields.notes
            if getattr(op.fields, "layout_style", None) is not None:
                slide.layout_style = op.fields.layout_style

        elif isinstance(op, MoveSlide):
            slide_idx = next(i for i, s in enumerate(working_deck.slides) if s.id == op.slide_id)
            slide = working_deck.slides.pop(slide_idx)
            working_deck.slides.insert(op.to_index, slide)

        elif isinstance(op, DeleteSlide):
            working_deck.slides = [s for s in working_deck.slides if s.id != op.slide_id]

        elif isinstance(op, AddSlide):
            after_idx = next(i for i, s in enumerate(working_deck.slides) if s.id == op.after_slide_id)
            image_ref = "diagram" if op.type == SlideType.ARCHITECTURE else None
            new_slide = Slide(
                id=uuid4().hex,
                type=op.type,
                title=op.content.title,
                subtitle=op.content.subtitle,
                bullets=list(op.content.bullets) if op.content.bullets is not None else [],
                image_ref=image_ref,
                notes=op.content.notes or "",
            )
            working_deck.slides.insert(after_idx + 1, new_slide)

        elif isinstance(op, SetDiagramFocus):
            if facts and facts.components:
                if not op.node_ids:
                    working_deck.diagram = facts.components.model_copy(deep=True)
                else:
                    kept_ids = set(op.node_ids)
                    kept_nodes = [
                        n.model_copy(deep=True)
                        for n in facts.components.nodes
                        if n.id in kept_ids
                    ]
                    kept_edges = [
                        e.model_copy(deep=True)
                        for e in facts.components.edges
                        if e.from_ in kept_ids and e.to in kept_ids
                    ]
                    working_deck.diagram = ComponentGraph(nodes=kept_nodes, edges=kept_edges)

        elif isinstance(op, Regenerate):
            slide = next(s for s in working_deck.slides if s.id == op.slide_id)
            capabilities_dict = manifest_capabilities(manifest).get(slide.type.value, {})
            prompt = load_prompt(
                "regenerate_slide",
                facts=facts.model_dump_json() if facts else "{}",
                slide_type=slide.type.value,
                current_slide=slide.model_dump_json(),
                capabilities=json.dumps(capabilities_dict),
            )
            regenerated = await generate_structured(prompt, Slide)
            regenerated.id = slide.id
            regenerated.type = slide.type
            # Run provenance rules of validate_deck on that slide
            warnings_tmp: list[str] = []
            _check_field(regenerated.title, regenerated.type, regenerated.id, "title", None, warnings_tmp)
            if regenerated.subtitle is not None:
                _check_field(regenerated.subtitle, regenerated.type, regenerated.id, "subtitle", None, warnings_tmp)
            for idx, b in enumerate(regenerated.bullets):
                _check_field(b, regenerated.type, regenerated.id, f"bullet:{idx}", None, warnings_tmp)
            slide_idx = next(i for i, s in enumerate(working_deck.slides) if s.id == op.slide_id)
            working_deck.slides[slide_idx] = regenerated

    # Increment version
    working_deck.version = deck.version + 1

    # Run validate_deck (known_paths=None, evidence check skipped)
    report = validate_deck(working_deck, manifest, known_paths=None)
    working_deck = report.deck

    # Enforce layout fit limits
    working_deck, warnings = await enforce_fit(working_deck, manifest, use_llm=True)

    summary = summarize_ops(deck, working_deck, ops)
    return ApplyResult(working_deck, summary, warnings)

