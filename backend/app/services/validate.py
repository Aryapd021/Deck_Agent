from dataclasses import dataclass
from uuid import uuid4

from app.config import get_settings
from app.models.api import FitLimits, TemplateManifest
from app.models.deck import Deck, Field, SlideType, Source


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


def find_fit_violations(deck: Deck, manifest: TemplateManifest) -> list[FitViolation]:
    violations: list[FitViolation] = []
    for slide in deck.slides:
        limits = manifest.limits.get(slide.type.value, FitLimits())

        # Title limit check
        if len(slide.title.text) > limits.title_max:
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
