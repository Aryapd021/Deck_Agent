from pathlib import Path
import struct
import zlib

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

from app.models.deck import SlideType
from app.services.render import render_deck
from app.services.template_inspector import inspect_template, resolve_picture_target
from tests.fixtures.sample_deck import build_sample_deck


def make_dummy_png(width: int, height: int) -> bytes:
    """Creates a valid, minimal in-memory RGB PNG."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw_data = b"".join(b"\x00" + b"\x40\x80\xc0" * width for _ in range(height))
    compressed = zlib.compress(raw_data)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def test_render_with_default_template(tmp_path: Path):
    template_path = tmp_path / "default_tmpl.pptx"
    prs_init = Presentation()
    prs_init.save(str(template_path))

    template_id = "test_tmpl_001"
    manifest = inspect_template(template_path, template_id, "Default Template")

    deck = build_sample_deck(template_id=template_id, project_id="test_render_project")
    assert len(deck.slides) == 10

    out_path = tmp_path / "rendered_deck.pptx"
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_path,
    )
    assert out_path.exists()

    prs = Presentation(str(out_path))
    assert len(prs.slides) == 10

    for i, (slide_model, slide_pptx) in enumerate(zip(deck.slides, prs.slides)):
        slot = manifest.slots[slide_model.type.value]
        title_ph = next(
            (ph for ph in slide_pptx.placeholders if ph.placeholder_format.idx == slot.title_idx),
            None,
        )
        assert title_ph is not None, f"Slide {i} missing title placeholder"
        assert title_ph.text == slide_model.title.text

        if slide_model.subtitle is not None and slot.subtitle_idx is not None:
            sub_ph = next(
                (ph for ph in slide_pptx.placeholders if ph.placeholder_format.idx == slot.subtitle_idx),
                None,
            )
            assert sub_ph is not None, f"Slide {i} missing subtitle placeholder"
            assert sub_ph.text == slide_model.subtitle.text

        assert slide_pptx.notes_slide.notes_text_frame.text == slide_model.notes

        for ph in slide_pptx.placeholders:
            if ph.has_text_frame:
                assert ph.text_frame.text.strip() != "", (
                    f"Slide {i} ({slide_model.type.value}) has empty placeholder idx {ph.placeholder_format.idx}"
                )


def test_resolve_picture_target(tmp_path: Path) -> None:
    template_path = tmp_path / "tmpl.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "t1", "Template")

    # Architecture layout in default template has picture placeholder
    arch_target = resolve_picture_target(manifest, "architecture")
    assert arch_target is not None
    assert arch_target.kind == "placeholder"
    assert arch_target.idx == 1
    assert arch_target.width > 0
    assert arch_target.height > 0
    assert arch_target.shrink_body is False

    # Problem layout in default template has body placeholder, no picture
    prob_target = resolve_picture_target(manifest, "problem")
    assert prob_target is not None
    assert prob_target.kind == "area"
    assert prob_target.idx == 1
    assert prob_target.shrink_body is True

    # Title layout has neither picture nor body placeholder
    title_target = resolve_picture_target(manifest, "title")
    assert title_target is not None
    assert title_target.kind == "area"
    assert title_target.idx is None
    assert title_target.shrink_body is False
    assert title_target.left == int(manifest.slide_width * 0.08)
    assert title_target.top == int(manifest.slide_height * 0.08)


def test_render_diagram_picture_insertion(tmp_path: Path) -> None:
    template_path = tmp_path / "default_tmpl.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "tmpl_diagram", "Default Template")

    # 800 x 400 PNG -> 2:1 aspect ratio
    png_w, png_h = 800, 400
    png_bytes = make_dummy_png(png_w, png_h)
    png_path = tmp_path / "diagram.png"
    png_path.write_bytes(png_bytes)

    deck = build_sample_deck(template_id="tmpl_diagram", project_id="diag_proj")
    out_path = tmp_path / "rendered_diagram_deck.pptx"

    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_path,
        diagram_png=png_path,
    )
    assert out_path.exists()

    prs = Presentation(str(out_path))
    assert len(prs.slides) == 10

    # Architecture slide is slide 4 (5th slide)
    arch_slide_model = next(s for s in deck.slides if s.type == SlideType.ARCHITECTURE)
    arch_slide_idx = deck.slides.index(arch_slide_model)
    arch_slide = prs.slides[arch_slide_idx]

    # Exactly one picture shape
    pictures = [s for s in arch_slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 1, f"Expected 1 picture shape on architecture slide, found {len(pictures)}"
    pic = pictures[0]

    # Aspect ratio matches PNG within 1 percent
    pic_ratio = pic.width / pic.height
    expected_ratio = png_w / png_h
    diff = abs(pic_ratio - expected_ratio) / expected_ratio
    assert diff < 0.01, f"Aspect ratio diff {diff:.4f} exceeds 1% limit"

    # Box lies inside slide
    assert pic.left >= 0
    assert pic.top >= 0
    assert pic.left + pic.width <= prs.slide_width
    assert pic.top + pic.height <= prs.slide_height

    # No empty picture placeholder remains
    for ph in arch_slide.placeholders:
        assert ph.placeholder_format.type != PP_PLACEHOLDER.PICTURE


def test_render_diagram_missing_silently_skipped(tmp_path: Path) -> None:
    template_path = tmp_path / "default_tmpl.pptx"
    Presentation().save(str(template_path))
    manifest = inspect_template(template_path, "tmpl_missing", "Default Template")

    deck = build_sample_deck(template_id="tmpl_missing", project_id="diag_proj")
    out_path = tmp_path / "deck_no_diagram.pptx"

    # None path and nonexistent path skip silently
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_path,
        diagram_png=None,
    )
    assert out_path.exists()

    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_path,
        diagram_png=tmp_path / "non_existent.png",
    )
    assert out_path.exists()
