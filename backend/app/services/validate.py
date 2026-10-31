from dataclasses import dataclass
import json
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.models.api import FitLimits, TemplateManifest
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.edit_ops import (
    AddSlide,
    DeleteSlide,
    EditOp,
    MoveSlide,
    Regenerate,
    SetDiagramFocus,
    ShortenResponse,
    UpdateSlide,
)
from app.models.facts import ProjectFacts
from app.services.llm.base import generate_structured, load_prompt



@dataclass
class FitViolation:
    slide_id: str
    field: str
    limit: int
    actual: int


@dataclass
class ValidationReport:
    deck: Deck
    warnings: list[str]


def check_slide_fit(slide: Slide, manifest: TemplateManifest) -> list[FitViolation]:
    limits = manifest.limits.get(
        slide.type.value if hasattr(slide.type, "value") else str(slide.type),
        FitLimits(),
    )
    violations: list[FitViolation] = []

    # Title limit check
    if slide.title and len(slide.title.text) > limits.title_max:
        violations.append(
            FitViolation(
                slide_id=slide.id,
                field="title",
                limit=limits.title_max,
                actual=len(slide.title.text),
            )
        )

    # Subtitle limit check
    if slide.subtitle is not None and len(slide.subtitle.text) > limits.bullet_max:
        violations.append(
            FitViolation(
                slide_id=slide.id,
                field="subtitle",
                limit=limits.bullet_max,
                actual=len(slide.subtitle.text),
            )
        )

    # Bullets count limit check
    if len(slide.bullets) > limits.bullets_max:
        violations.append(
            FitViolation(
                slide_id=slide.id,
                field="bullets_count",
                limit=limits.bullets_max,
                actual=len(slide.bullets),
            )
        )

    # Individual bullet length limit check
    for idx, bullet in enumerate(slide.bullets):
        if len(bullet.text) > limits.bullet_max:
            violations.append(
                FitViolation(
                    slide_id=slide.id,
                    field=f"bullet:{idx}",
                    limit=limits.bullet_max,
                    actual=len(bullet.text),
                )
            )

    # Notes limit check
    if len(slide.notes) > limits.notes_max:
        violations.append(
            FitViolation(
                slide_id=slide.id,
                field="notes",
                limit=limits.notes_max,
                actual=len(slide.notes),
            )
        )

    return violations


def check_op_fit(op: EditOp, deck: Deck, manifest: TemplateManifest) -> str | None:
    """Checks whether an op introduces fit violations on the affected slide."""
    if isinstance(op, UpdateSlide):
        slide = next((s for s in deck.slides if s.id == op.slide_id), None)
        if not slide:
            return None
        st_key = slide.type.value if hasattr(slide.type, "value") else str(slide.type)
        limits = manifest.limits.get(st_key, FitLimits())
        if op.fields.title and len(op.fields.title.text) > limits.title_max:
            return f"Title length {len(op.fields.title.text)} exceeds limit {limits.title_max}"
        if op.fields.subtitle and len(op.fields.subtitle.text) > limits.bullet_max:
            return f"Subtitle length {len(op.fields.subtitle.text)} exceeds limit {limits.bullet_max}"
        if op.fields.bullets is not None:
            if len(op.fields.bullets) > limits.bullets_max:
                return f"Bullets count {len(op.fields.bullets)} exceeds limit {limits.bullets_max}"
            for i, b in enumerate(op.fields.bullets):
                if len(b.text) > limits.bullet_max:
                    return f"Bullet {i} length {len(b.text)} exceeds limit {limits.bullet_max}"
        if op.fields.notes and len(op.fields.notes) > limits.notes_max:
            return f"Notes length {len(op.fields.notes)} exceeds limit {limits.notes_max}"

    elif isinstance(op, AddSlide):
        st_key = op.type.value if hasattr(op.type, "value") else str(op.type)
        limits = manifest.limits.get(st_key, FitLimits())
        if op.content.title and len(op.content.title.text) > limits.title_max:
            return f"Title length {len(op.content.title.text)} exceeds limit {limits.title_max}"
        if op.content.subtitle and len(op.content.subtitle.text) > limits.bullet_max:
            return f"Subtitle length {len(op.content.subtitle.text)} exceeds limit {limits.bullet_max}"
        if op.content.bullets is not None:
            if len(op.content.bullets) > limits.bullets_max:
                return f"Bullets count {len(op.content.bullets)} exceeds limit {limits.bullets_max}"
            for i, b in enumerate(op.content.bullets):
                if len(b.text) > limits.bullet_max:
                    return f"Bullet {i} length {len(b.text)} exceeds limit {limits.bullet_max}"
        if op.content.notes and len(op.content.notes) > limits.notes_max:
            return f"Notes length {len(op.content.notes)} exceeds limit {limits.notes_max}"

    return None


def check_op(
    deck: Deck,
    op: EditOp,
    facts: ProjectFacts | None,
    max_slides: int,
) -> str | None:
    slide_ids = {s.id for s in deck.slides}

    if isinstance(op, UpdateSlide):
        if op.slide_id not in slide_ids:
            return f"Slide {op.slide_id} not found"

    elif isinstance(op, MoveSlide):
        if op.slide_id not in slide_ids:
            return f"Slide {op.slide_id} not found"
        if op.to_index < 0 or op.to_index >= len(deck.slides):
            return f"to_index {op.to_index} out of range [0, {len(deck.slides) - 1}]"

    elif isinstance(op, DeleteSlide):
        if op.slide_id not in slide_ids:
            return f"Slide {op.slide_id} not found"
        if len(deck.slides) <= 2:
            return "Delete refused: deck would have fewer than 2 slides"

    elif isinstance(op, AddSlide):
        if op.after_slide_id not in slide_ids:
            return f"Slide {op.after_slide_id} not found"
        if len(deck.slides) >= max_slides:
            return f"Cannot add slide: deck already at maximum {max_slides} slides"
        try:
            if isinstance(op.type, SlideType):
                pass
            else:
                SlideType(op.type)
        except (ValueError, TypeError):
            return f"Invalid slide type: {op.type}"
        if op.content is None or op.content.title is None or not op.content.title.text.strip():
            return "Add slide requires title in content"

    elif isinstance(op, SetDiagramFocus):
        if facts is None or facts.components is None:
            return "Diagram focus requires project facts"
        fact_nodes = {n.id for n in facts.components.nodes}
        missing = [nid for nid in op.node_ids if nid not in fact_nodes]
        if missing:
            return f"Diagram node ids not found in project facts: {', '.join(missing)}"
        if op.node_ids:
            unique_nodes = set(op.node_ids)
            if len(unique_nodes) < 4:
                return "The diagram needs at least 4 components"
            if len(unique_nodes) > 8:
                return "The diagram can have at most 8 components"

    elif isinstance(op, Regenerate):
        if op.slide_id not in slide_ids:
            return f"Slide {op.slide_id} not found"
        if facts is None:
            return "Regenerating a slide requires project facts"

    return None


def find_fit_violations(deck: Deck, manifest: TemplateManifest) -> list[FitViolation]:
    violations: list[FitViolation] = []
    for slide in deck.slides:
        violations.extend(check_slide_fit(slide, manifest))
    return violations


def _check_field(
    field: Field,
    slide_type: SlideType,
    slide_id: str,
    field_name: str,
    known_paths: set[str] | None,
    warnings: list[str],
) -> None:
    # (a) Repo-sourced field whose evidence is missing, or not in known_paths
    if field.source == Source.REPO:
        if not field.evidence or (known_paths is not None and field.evidence not in known_paths):
            field.source = Source.NEEDS_INPUT
            if not field.text.startswith("TODO:"):
                field.text = f"TODO: verify {field.text}"
            field.evidence = None
            warnings.append(
                f"Slide {slide_id} {field_name} (repo) missing or invalid evidence; demoted to needs_input."
            )

    # (b) Chat-sourced field on a slide type other than problem/solution
    elif field.source == Source.CHAT:
        if slide_type not in (SlideType.PROBLEM, SlideType.SOLUTION):
            field.source = Source.NEEDS_INPUT
            if not field.text.startswith("TODO:"):
                field.text = f"TODO: {field.text}"
            field.evidence = None
            warnings.append(
                f"Slide {slide_id} {field_name} (chat) disallowed on slide type '{slide_type.value}'; demoted to needs_input."
            )

    # (c) needs_input fields lose their evidence
    if field.source == Source.NEEDS_INPUT:
        if field.evidence is not None:
            warnings.append(
                f"Slide {slide_id} {field_name} (needs_input) stray evidence '{field.evidence}' stripped."
            )
            field.evidence = None


def validate_deck(
    deck: Deck,
    manifest: TemplateManifest,
    known_paths: set[str] | None = None,
) -> ValidationReport:
    corrected_deck = deck.model_copy(deep=True)
    warnings: list[str] = []
    settings = get_settings()

    # Structural check 1: Slide IDs unique
    seen_ids: set[str] = set()
    for s in corrected_deck.slides:
        if s.id in seen_ids:
            old_id = s.id
            s.id = uuid4().hex
            warnings.append(f"Duplicate slide id {old_id} replaced with {s.id}")
        seen_ids.add(s.id)

    # Structural check 2: Slide count <= max_slides
    if len(corrected_deck.slides) > settings.max_slides:
        orig_count = len(corrected_deck.slides)
        corrected_deck.slides = corrected_deck.slides[:settings.max_slides]
        warnings.append(
            f"Slide count ({orig_count}) exceeded max_slides ({settings.max_slides}); truncated."
        )

    # Structural check 3: <= 5 bullets
    for s in corrected_deck.slides:
        if len(s.bullets) > 5:
            s.bullets = s.bullets[:5]
            warnings.append(f"Slide {s.id} bullets count exceeded 5; truncated.")

    # Provenance checks
    for s in corrected_deck.slides:
        _check_field(s.title, s.type, s.id, "title", known_paths, warnings)
        if s.subtitle is not None:
            _check_field(s.subtitle, s.type, s.id, "subtitle", known_paths, warnings)
        for idx, bullet in enumerate(s.bullets):
            _check_field(bullet, s.type, s.id, f"bullet:{idx}", known_paths, warnings)

    # Fit violations reported as warnings only for now
    fit_violations = find_fit_violations(corrected_deck, manifest)
    for fv in fit_violations:
        warnings.append(
            f"Fit violation on slide {fv.slide_id} for field {fv.field}: actual length/count {fv.actual} exceeds limit {fv.limit}."
        )

    return ValidationReport(deck=corrected_deck, warnings=warnings)


def validate_generated_slide_text(deck: Deck) -> None:
    """Rejects any generated slide text that begins with 'TODO:' or is shorter than 40 characters (except titles/headings)."""
    for slide in deck.slides:
        for idx, b in enumerate(slide.bullets):
            text = b.text.strip()
            if text.startswith("TODO:") or text.startswith("TODO"):
                raise ApiError(
                    ErrorCode.LLM_INVALID_OUTPUT,
                    f"Slide '{slide.type.value}' bullet {idx+1} begins with banned 'TODO:': '{text[:30]}...'",
                )
            if len(text) < 40:
                raise ApiError(
                    ErrorCode.LLM_INVALID_OUTPUT,
                    f"Slide '{slide.type.value}' bullet {idx+1} has length {len(text)} < 40 chars: '{text}'",
                )
        if slide.summary and (slide.summary.text.strip().startswith("TODO:") or slide.summary.text.strip().startswith("TODO")):
            raise ApiError(
                ErrorCode.LLM_INVALID_OUTPUT,
                f"Slide '{slide.type.value}' summary begins with banned 'TODO:'",
            )
        if slide.takeaway and (slide.takeaway.text.strip().startswith("TODO:") or slide.takeaway.text.strip().startswith("TODO")):
            raise ApiError(
                ErrorCode.LLM_INVALID_OUTPUT,
                f"Slide '{slide.type.value}' takeaway begins with banned 'TODO:'",
            )


def _truncate_at_word_boundary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    ellipsis = "..."
    if limit <= len(ellipsis):
        return text[:limit]
    budget = limit - len(ellipsis)
    candidate = text[:budget]
    last_space = candidate.rfind(" ")
    if last_space > 0:
        chopped = candidate[:last_space].rstrip()
        if chopped:
            return chopped + ellipsis
    return candidate.rstrip() + ellipsis


async def enforce_fit(
    deck: Deck,
    manifest: TemplateManifest,
    use_llm: bool = True,
) -> tuple[Deck, list[str]]:
    violations = find_fit_violations(deck, manifest)
    if not violations:
        return deck, []

    working_deck = deck.model_copy(deep=True)
    warnings: list[str] = []
    slide_map = {s.id: s for s in working_deck.slides}

    if use_llm:
        items_to_shorten: list[dict[str, Any]] = []
        for v in violations:
            slide = slide_map.get(v.slide_id)
            if not slide:
                continue
            limits = manifest.limits.get(
                slide.type.value if hasattr(slide.type, "value") else str(slide.type),
                FitLimits(),
            )
            if v.field == "title" and slide.title:
                items_to_shorten.append({
                    "slide_id": v.slide_id,
                    "field": "title",
                    "current_text": slide.title.text,
                    "max_chars": limits.title_max,
                })
            elif v.field == "subtitle" and slide.subtitle:
                items_to_shorten.append({
                    "slide_id": v.slide_id,
                    "field": "subtitle",
                    "current_text": slide.subtitle.text,
                    "max_chars": limits.bullet_max,
                })
            elif v.field.startswith("bullet:"):
                idx_str = v.field.split(":")[1]
                if idx_str.isdigit():
                    idx = int(idx_str)
                    if 0 <= idx < len(slide.bullets) and idx < limits.bullets_max:
                        items_to_shorten.append({
                            "slide_id": v.slide_id,
                            "field": v.field,
                            "current_text": slide.bullets[idx].text,
                            "max_chars": limits.bullet_max,
                        })
            elif v.field == "notes" and slide.notes:
                items_to_shorten.append({
                    "slide_id": v.slide_id,
                    "field": "notes",
                    "current_text": slide.notes,
                    "max_chars": limits.notes_max,
                })

        if items_to_shorten:
            violations_json = json.dumps(items_to_shorten, indent=2)
            prompt = load_prompt("shorten", violations=violations_json)
            try:
                shorten_resp = await generate_structured(prompt, ShortenResponse)
            except ApiError:
                shorten_resp = None

            if shorten_resp and shorten_resp.items:
                for item in shorten_resp.items:
                    slide = slide_map.get(item.slide_id)
                    if not slide:
                        continue
                    limits = manifest.limits.get(
                        slide.type.value if hasattr(slide.type, "value") else str(slide.type),
                        FitLimits(),
                    )
                    text = item.text.strip() if item.text else ""
                    if not text:
                        continue

                    if item.field == "title":
                        if slide.title and len(text) <= limits.title_max:
                            slide.title.text = text
                    elif item.field == "subtitle":
                        if slide.subtitle and len(text) <= limits.bullet_max:
                            slide.subtitle.text = text
                    elif item.field.startswith("bullet:"):
                        idx_str = item.field.split(":")[1]
                        if idx_str.isdigit():
                            idx = int(idx_str)
                            if 0 <= idx < len(slide.bullets) and len(text) <= limits.bullet_max:
                                slide.bullets[idx].text = text
                    elif item.field == "notes":
                        if len(text) <= limits.notes_max:
                            slide.notes = text

    # Deterministic fixes for whatever still violates afterwards
    for n, slide in enumerate(working_deck.slides, start=1):
        limits = manifest.limits.get(
            slide.type.value if hasattr(slide.type, "value") else str(slide.type),
            FitLimits(),
        )

        # Title
        if slide.title and len(slide.title.text) > limits.title_max:
            slide.title.text = _truncate_at_word_boundary(slide.title.text, limits.title_max)
            warnings.append(f"Slide {n} title truncated")

        # Subtitle
        if slide.subtitle is not None and len(slide.subtitle.text) > limits.bullet_max:
            slide.subtitle.text = _truncate_at_word_boundary(slide.subtitle.text, limits.bullet_max)
            warnings.append(f"Slide {n} subtitle truncated")

        # Bullets count
        if len(slide.bullets) > limits.bullets_max:
            slide.bullets = slide.bullets[:limits.bullets_max]
            warnings.append(f"Slide {n} bullets truncated")

        # Individual bullets
        for b_idx, bullet in enumerate(slide.bullets):
            if len(bullet.text) > limits.bullet_max:
                bullet.text = _truncate_at_word_boundary(bullet.text, limits.bullet_max)
                warnings.append(f"Slide {n} bullet {b_idx + 1} truncated")

        # Notes
        if len(slide.notes) > limits.notes_max:
            slide.notes = _truncate_at_word_boundary(slide.notes, limits.notes_max)
            warnings.append(f"Slide {n} notes truncated")

    return working_deck, warnings

