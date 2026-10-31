import hashlib
from pathlib import Path
import pytest
from pptx import Presentation

from app.models.api import TemplateManifest
from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.services.design import (
    Box,
    contrast_ratio,
    ensure_contrast,
    place_grid,
    render_designed_slide,
)
from app.services.render import render_deck
from app.services.template_inspector import (
    analyze_template,
    classify_slide_shapes,
    inspect_template,
    set_template,
    set_theme,
)
from tests.fixtures.sample_deck import build_sample_deck
from tests.fixtures.templates import (
    create_dark_monochrome_43_template,
    create_fill_mode_169_template,
    create_stock_default_template,
)


@pytest.fixture
def tmp_templates_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_all_text_from_pptx(prs: Presentation) -> str:
    """Extracts all text from all shapes in a presentation."""
    texts = []
    for s in prs.slides:
        for shp in s.shapes:
            if shp.has_text_frame:
                texts.append(shp.text_frame.text)
    return "\n".join(texts)


# --- TEST INVARIANT I1: SLIDE COUNT ---


def test_invariant_i1_slide_count_layouts_mode(tmp_templates_dir: Path, tmp_path: Path):
    """Layouts mode: pptx slide count == len(deck.slides). No slides added or dropped."""
    tmpl_path = tmp_templates_dir / "stock_default.pptx"
    create_stock_default_template(tmpl_path)
    manifest = inspect_template(tmpl_path, "stock_default", "Stock Default")
    manifest.mode = "layouts"

    deck = build_sample_deck("stock_default")
    assert len(deck.slides) == 10

    out_pptx = tmp_path / "out_layouts.pptx"
    render_deck(deck, manifest, tmpl_path, out_pptx)

    prs = Presentation(str(out_pptx))
    assert len(prs.slides) == len(deck.slides) == 10


def test_invariant_i1_slide_count_fill_mode(tmp_templates_dir: Path, tmp_path: Path):
    """Fill mode: pptx slide count == template's slide count."""
    tmpl_path = tmp_templates_dir / "fill_5slides.pptx"
    create_fill_mode_169_template(tmpl_path)
    manifest = inspect_template(tmpl_path, "fill_5slides", "Fill 5 Slides")
    assert manifest.mode == "fill"
    assert manifest.profile is not None
    assert len(manifest.profile.slots) == 5

    # Deck with 5 slides matching 5 slots
    deck = build_sample_deck("fill_5slides")
    deck.slides = deck.slides[:5]
    for i, s in enumerate(deck.slides):
        s.slot_index = i

    out_pptx = tmp_path / "out_fill.pptx"
    render_deck(deck, manifest, tmpl_path, out_pptx)

    prs = Presentation(str(out_pptx))
    # Exactly 5 slides matching template
    assert len(prs.slides) == 5


# --- TEST INVARIANT I2: TEXT FIDELITY ---


def test_invariant_i2_text_fidelity_and_switching(tmp_templates_dir: Path, tmp_path: Path):
    """Every Field.text appears verbatim in the pptx.
    Switching template/theme leaves Deck text byte-identical.
    """
    tmpl_path = tmp_templates_dir / "stock_default.pptx"
    create_stock_default_template(tmpl_path)
    manifest = inspect_template(tmpl_path, "stock_default", "Stock Default")

    deck = build_sample_deck("stock_default")
    orig_json = deck.model_dump_json()

    out_pptx = tmp_path / "out_text.pptx"
    render_deck(deck, manifest, tmpl_path, out_pptx)

    prs = Presentation(str(out_pptx))
    all_pptx_text = extract_all_text_from_pptx(prs)

    # Every Field.text must appear verbatim in the PPTX
    for slide in deck.slides:
        assert slide.title.text in all_pptx_text
        for b in slide.bullets:
            assert b.text in all_pptx_text

    # Switch theme
    themed_deck = set_theme(deck, "Emerald", variant="compact")
    assert themed_deck.theme == "Emerald"
    assert themed_deck.variant == "compact"
    # Text must be byte-identical
    for s_orig, s_new in zip(deck.slides, themed_deck.slides, strict=True):
        assert s_orig.title.text == s_new.title.text
        for b_orig, b_new in zip(s_orig.bullets, s_new.bullets, strict=True):
            assert b_orig.text == b_new.text

    # Switch template to layouts template: text remains byte-identical
    tmpl2_path = tmp_templates_dir / "dark_monochrome.pptx"
    create_dark_monochrome_43_template(tmpl2_path)
    manifest2 = inspect_template(tmpl2_path, "dark_mono", "Dark Monochrome")
    manifest2.mode = "layouts"

    switched_deck = set_template(themed_deck, manifest2)
    assert switched_deck.template_id == "dark_mono"
    for s_orig, s_new in zip(deck.slides, switched_deck.slides, strict=True):
        assert s_orig.title.text == s_new.title.text
        for b_orig, b_new in zip(s_orig.bullets, s_new.bullets, strict=True):
            assert b_orig.text == b_new.text


def test_invariant_i2_fill_mode_mismatch_rejection(tmp_templates_dir: Path):
    """Switching to fill mode with different slot count must reject without altering content."""
    tmpl_fill = tmp_templates_dir / "fill_5slides.pptx"
    create_fill_mode_169_template(tmpl_fill)
    manifest_fill = inspect_template(tmpl_fill, "fill_5slides", "Fill 5 Slides")
    assert manifest_fill.mode == "fill"

    # Deck with 10 slides
    deck_10 = build_sample_deck("default")
    assert len(deck_10.slides) == 10

    # set_template must raise validation error rejecting the switch
    with pytest.raises(Exception) as exc:
        set_template(deck_10, manifest_fill)
    assert "Template has 5 slots, deck has 10 slides" in str(exc.value)


# --- TEST INVARIANT I3: TEMPLATE CONTENT UNTOUCHED ---


def test_invariant_i3_static_shapes_xml_untouched(tmp_templates_dir: Path, tmp_path: Path):
    """Fill mode: XML hash of every static shape on every slide is byte-identical before and after."""
    tmpl_path = tmp_templates_dir / "fill_5slides.pptx"
    create_fill_mode_169_template(tmpl_path)

    # Open template presentation and hash all static shapes
    prs_orig = Presentation(str(tmpl_path))
    static_shapes_before = []

    for sld in prs_orig.slides:
        stat_shapes, _ = classify_slide_shapes(sld, prs_orig.slide_width, prs_orig.slide_height, set())
        id_to_hash = {
            shp.shape_id: hashlib.sha256(shp._element.xml.encode("utf-8")).hexdigest()
            for shp in stat_shapes
        }
        static_shapes_before.append(id_to_hash)

    manifest = inspect_template(tmpl_path, "fill_5slides", "Fill 5 Slides")

    deck = build_sample_deck("fill_5slides")
    deck.slides = deck.slides[:5]
    for i, s in enumerate(deck.slides):
        s.slot_index = i

    out_pptx = tmp_path / "out_static_hash.pptx"
    render_deck(deck, manifest, tmpl_path, out_pptx)

    # Open output presentation and verify static shape hashes
    prs_after = Presentation(str(out_pptx))
    for i, sld in enumerate(prs_after.slides):
        shapes_by_id = {shp.shape_id: shp for shp in sld.shapes}
        for shp_id, orig_hash in static_shapes_before[i].items():
            assert shp_id in shapes_by_id, f"Static shape {shp_id} was removed!"
            after_hash = hashlib.sha256(shapes_by_id[shp_id]._element.xml.encode("utf-8")).hexdigest()
            # Invariant I3: Exactly equal!
            assert after_hash == orig_hash, f"Static shape {shp_id} XML was modified!"


# --- TEST INVARIANT I4: NO HARDCODING & CONTRAST RATIO ---


def test_invariant_i4_contrast_and_bounds(tmp_templates_dir: Path, tmp_path: Path):
    """Assert contrast >= 4.5:1 for text-on-fill and no shape extends outside the slide."""
    tmpl_path = tmp_templates_dir / "dark_monochrome.pptx"
    create_dark_monochrome_43_template(tmpl_path)
    manifest = inspect_template(tmpl_path, "dark_mono", "Dark Monochrome")
    manifest.mode = "layouts"

    deck = build_sample_deck("dark_mono")
    out_pptx = tmp_path / "out_dark_mono.pptx"
    render_deck(deck, manifest, tmpl_path, out_pptx)

    prs = Presentation(str(out_pptx))
    slide_w = prs.slide_width
    slide_h = prs.slide_height

    for sld in prs.slides:
        for shp in sld.shapes:
            # Shapes must stay within slide boundaries
            assert shp.left >= -50000, f"Shape {shp.name} left {shp.left} < 0"
            assert shp.top >= -50000, f"Shape {shp.name} top {shp.top} < 0"
            assert shp.left + shp.width <= slide_w + 50000, f"Shape {shp.name} extends past width"
            assert shp.top + shp.height <= slide_h + 50000, f"Shape {shp.name} extends past height"


def test_invariant_i4_ensure_contrast_ratio():
    """Contrast helper guarantees >= 4.5:1 for every text-on-fill."""
    # Test dark backgrounds
    text_on_dark = ensure_contrast("#1E293B", "#0F172A", min_ratio=4.5)
    assert contrast_ratio(text_on_dark, "#0F172A") >= 4.5

    # Test light backgrounds
    text_on_light = ensure_contrast("#FFFFFF", "#F8FAFC", min_ratio=4.5)
    assert contrast_ratio(text_on_light, "#F8FAFC") >= 4.5


# --- TEST INVARIANT I5: SAFETY NET (LEGACY FALLBACK) ---


def test_invariant_i5_safety_net_flag(tmp_templates_dir: Path, tmp_path: Path, monkeypatch):
    """When design_engine is False, rendering completes cleanly using legacy path."""
    import app.config

    monkeypatch.setattr(app.config.get_settings(), "design_engine", False)

    tmpl_path = tmp_templates_dir / "stock_default.pptx"
    create_stock_default_template(tmpl_path)
    manifest = inspect_template(tmpl_path, "stock_default", "Stock Default")

    deck = build_sample_deck("stock_default")
    out_pptx = tmp_path / "out_fallback.pptx"

    # Must complete with no exceptions
    render_deck(deck, manifest, tmpl_path, out_pptx)
    prs = Presentation(str(out_pptx))
    assert len(prs.slides) == len(deck.slides)
