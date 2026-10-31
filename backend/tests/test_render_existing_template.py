import json
from pathlib import Path
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.models.deck import Deck
from app.services.render import render_deck
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck
from tests.test_render import make_dummy_png


def test_render_with_pre_existing_template_slides(tmp_path: Path):
    # Use SIH custom template
    template_src = Path("templates/8eb41dac11974621833969b5dd7b05af.pptx")
    assert template_src.exists(), "SIH template missing"

    template_copy = tmp_path / "custom_template.pptx"
    template_copy.write_bytes(template_src.read_bytes())

    manifest = inspect_template(template_copy, "test_custom_sih", "SIH Template")

    deck = build_sample_deck(template_id="test_custom_sih", project_id="p_test_custom")
    assert len(deck.slides) == 10

    # Create dummy diagram png
    diag_png = tmp_path / "diagram.png"
    diag_png.write_bytes(make_dummy_png(400, 300))

    out_pptx = tmp_path / "rendered_custom.pptx"
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_copy,
        out_path=out_pptx,
        diagram_png=diag_png,
    )

    assert out_pptx.exists()
    prs = Presentation(str(out_pptx))

    # The 7-slide template should have its 7th instruction slide removed -> 6 slides
    assert len(prs.slides) == 6

    # Slide 1: Title & Subtitle
    s1 = prs.slides[0]
    s1_titles = [
        s.text_frame.text
        for s in s1.shapes
        if s.has_text_frame and s.text_frame.text.strip()
    ]
    assert any("DeckAgent" in t for t in s1_titles)

    # Slide 3: Technical approach should contain diagram picture
    s3 = prs.slides[2]
    pics = [s for s in s3.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pics) >= 1, "Technical approach slide should contain diagram picture"

    # Slide 2 to 6: Body content should be populated with bullets
    for s_idx in range(1, 6):
        slide = prs.slides[s_idx]
        has_bullets = any(
            "•" in s.text_frame.text or len(s.text_frame.paragraphs) > 1
            for s in slide.shapes
            if s.has_text_frame
        )
        assert has_bullets, f"Slide {s_idx+1} should have populated body bullets"
