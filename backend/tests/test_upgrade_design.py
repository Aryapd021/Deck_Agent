from pathlib import Path
import pytest
from pptx import Presentation
from pptx.util import Inches

from app.config import get_settings
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.services.design import (
    SLIDE_HEIGHT_EMU,
    SLIDE_WIDTH_EMU,
    is_all_needs_input,
)
from app.services.render import render_deck
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck


def test_design_engine_true_render_and_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "design_engine", True)

    template_path = tmp_path / "default.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "tmpl_1", "Default Template")

    deck = build_sample_deck(template_id="tmpl_1", project_id="TestProject")
    out_pptx = tmp_path / "rendered_design_true.pptx"

    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_pptx,
    )

    assert out_pptx.exists()
    prs = Presentation(str(out_pptx))

    # All 10 slides render
    assert len(prs.slides) == 10

    # Canvas dimensions are 16:9 (13.333 x 7.5 in)
    assert prs.slide_width == SLIDE_WIDTH_EMU
    assert prs.slide_height == SLIDE_HEIGHT_EMU

    # Check bounds and verify no text box extends past the slide
    # Allow small 0.05in margin of error for rounding
    slack = Inches(0.05)
    for s_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            assert shape.left >= -slack, f"Slide {s_idx} shape extends left: {shape.left}"
            assert shape.top >= -slack, f"Slide {s_idx} shape extends top: {shape.top}"
            assert shape.left + shape.width <= prs.slide_width + slack, (
                f"Slide {s_idx} shape width extends right: {shape.left + shape.width} > {prs.slide_width}"
            )
            assert shape.top + shape.height <= prs.slide_height + slack, (
                f"Slide {s_idx} shape height extends bottom: {shape.top + shape.height} > {prs.slide_height}"
            )

        # Bounds and geometry verified
        pass


def test_design_engine_false_render_and_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "design_engine", False)

    template_path = tmp_path / "default.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "tmpl_1", "Default Template")

    deck = build_sample_deck(template_id="tmpl_1", project_id="TestProject")
    out_pptx = tmp_path / "rendered_design_false.pptx"

    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_pptx,
    )

    assert out_pptx.exists()
    prs = Presentation(str(out_pptx))
    assert len(prs.slides) == 10


def test_all_needs_input_slide_rendered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UPGRADE-3: Every Deck slide is always rendered, even when all bullets are needs_input."""
    settings = get_settings()
    monkeypatch.setattr(settings, "design_engine", True)

    template_path = tmp_path / "default.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "tmpl_1", "Default Template")

    deck = build_sample_deck(template_id="tmpl_1", project_id="TestProject")
    # Replace slide 1 (problem slide) bullets with all needs_input bullets
    deck.slides[1].bullets = [
        Field(text="TODO: Add customer quotes on pain point", source=Source.NEEDS_INPUT),
        Field(text="Add: quantitative churn rate metric", source=Source.NEEDS_INPUT),
    ]

    out_pptx = tmp_path / "rendered_all_ni_slide.pptx"
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_pptx,
    )

    assert out_pptx.exists()
    prs = Presentation(str(out_pptx))

    # In UPGRADE-3: Every Deck slide is always rendered (10 slides)
    assert len(prs.slides) == 10


def test_design_fallback_safety_net(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "design_engine", True)

    template_path = tmp_path / "default.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "tmpl_1", "Default Template")

    deck = build_sample_deck(template_id="tmpl_1", project_id="TestProject")

    # Monkeypatch render_designed_slide to raise an exception on slide 2
    from app.services import design

    original_render_designed = design.render_designed_slide
    call_count = {"val": 0}
    fallback_emitted = []

    def mock_exploding_render(*args, **kwargs):
        call_count["val"] += 1
        if call_count["val"] == 2:
            raise RuntimeError("Simulated design rendering crash")
        return original_render_designed(*args, **kwargs)

    monkeypatch.setattr("app.services.design.render_designed_slide", mock_exploding_render)
    monkeypatch.setattr("app.services.render.render_designed_slide", mock_exploding_render)

    out_pptx = tmp_path / "rendered_fallback.pptx"

    def _progress_callback(msg: str) -> None:
        fallback_emitted.append(msg)

    # Rendering MUST NEVER fail because of styling!
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_pptx,
        on_progress=_progress_callback,
    )

    assert out_pptx.exists()
    prs = Presentation(str(out_pptx))
    assert len(prs.slides) == 10
    assert "Design fallback" in fallback_emitted
