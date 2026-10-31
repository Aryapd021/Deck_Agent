import pytest

from app.core.errors import ApiError, ErrorCode
from app.models.deck import Deck, Slide, Field, Source, SlideType
from app.models.api import TemplateManifest
from app.services.validate import validate_generated_slide_text, validate_deck


def test_field_heading_and_slide_summary_takeaway_contract() -> None:
    # 1. New rich content fields
    f = Field(
        text="Transforms dense adjacency matrices into Compressed Sparse Row format for O(1) row lookups.",
        source=Source.REPO,
        heading="CSR Matrix Conversion",
    )
    assert f.heading == "CSR Matrix Conversion"

    s = Slide(
        id="slide_rich",
        type=SlideType.PROBLEM,
        title=Field(text="High Sparse Memory Overhead", source=Source.REPO),
        summary=Field(text="Standard graph representations waste 80% memory on zero-weight edges.", source=Source.REPO),
        bullets=[f],
        takeaway=Field(text="Reduces RAM footprint by 75% while boosting traversal speed 3.2x.", source=Source.REPO),
    )
    assert s.summary is not None
    assert s.summary.text.startswith("Standard graph")
    assert s.takeaway is not None
    assert s.takeaway.text.startswith("Reduces RAM")

    # 2. Backward compatibility: old dictionary without heading, summary, takeaway
    old_data = {
        "id": "slide_old",
        "type": "features",
        "title": {"text": "Legacy Slide Title", "source": "repo"},
        "bullets": [{"text": "Legacy bullet without explicit heading but enough chars.", "source": "repo"}],
    }
    s_old = Slide.model_validate(old_data)
    assert s_old.summary is None
    assert s_old.takeaway is None
    assert s_old.bullets[0].heading is None


def test_validate_rejects_todo_and_short_bullets() -> None:
    # Banned 'TODO:' prefix
    deck_with_todo = Deck(
        schema_version=2,
        project_id="p1",
        version=1,
        template_id="tmpl_1",
        slides=[
            Slide(
                type=SlideType.FEATURES,
                title=Field(text="Core Architecture", source=Source.REPO),
                bullets=[
                    Field(text="TODO: integrate benchmark suite for validation", source=Source.NEEDS_INPUT),
                ],
            )
        ],
    )
    with pytest.raises(ApiError) as exc_info:
        validate_generated_slide_text(deck_with_todo)
    assert exc_info.value.code == ErrorCode.LLM_INVALID_OUTPUT
    assert "TODO" in str(exc_info.value.message)

    # Short bullet (< 40 chars)
    deck_with_short = Deck(
        schema_version=2,
        project_id="p1",
        version=1,
        template_id="tmpl_1",
        slides=[
            Slide(
                type=SlideType.FEATURES,
                title=Field(text="Core Architecture", source=Source.REPO),
                bullets=[
                    Field(text="Short bullet text.", source=Source.REPO),
                ],
            )
        ],
    )
    with pytest.raises(ApiError) as exc_info2:
        validate_generated_slide_text(deck_with_short)
    assert exc_info2.value.code == ErrorCode.LLM_INVALID_OUTPUT
    assert "< 40 chars" in str(exc_info2.value.message)

    # Valid rich deck passes cleanly
    deck_valid = Deck(
        schema_version=2,
        project_id="p1",
        version=1,
        template_id="tmpl_1",
        slides=[
            Slide(
                type=SlideType.FEATURES,
                title=Field(text="Core Architecture", source=Source.REPO),
                bullets=[
                    Field(
                        text="Automated continuous integration pipeline validates and builds containers on every push.",
                        source=Source.REPO,
                        heading="CI/CD Pipeline",
                    ),
                ],
            )
        ],
    )
    validate_generated_slide_text(deck_valid)
