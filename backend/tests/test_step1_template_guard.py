import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
from PIL import Image as PILImage
import zipfile

from app.config import get_settings
from app.db import init_db
from app.main import app
from app.models.deck import Deck
from app.services.render import render_deck
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck


def create_fixture_template_with_logo_and_5_layouts(path: Path) -> Path:
    prs = Presentation()
    prs.slide_width = 12192000
    prs.slide_height = 6858000

    # Create dummy logo
    logo_path = path.parent / f"{path.stem}_logo.png"
    img = PILImage.new("RGB", (80, 80), color=(220, 50, 30))
    img.save(str(logo_path), format="PNG")

    # Ensure 5 distinct custom layouts
    layout_names = [
        "Executive Summary Layout",
        "Problem Deep-Dive Layout",
        "Architecture System Layout",
        "Market Traction Layout",
        "Financial Projections Layout",
    ]
    while len(prs.slide_layouts) < 5:
        # Stock python-pptx has 11 layouts anyway
        pass

    for i in range(5):
        prs.slide_layouts[i].name = layout_names[i]

    # Add 5 template slides with static logo and fillable box
    for i in range(5):
        slide = prs.slides.add_slide(prs.slide_layouts[i])
        # Add logo picture to slide
        slide.shapes.add_picture(str(logo_path), Inches(11.0), Inches(0.4), Inches(1.5), Inches(0.8))
        # Add title
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(8.0), Inches(0.8))
        tb.text_frame.text = f"Static Title {i+1}"
        # Add sample body
        body = slide.shapes.add_textbox(Inches(0.5), Inches(1.8), Inches(11.0), Inches(4.5))
        body.text_frame.text = f"Sample body text on slide {i+1}"

    prs.save(str(path))
    return path


def test_guard_asserts_template_fidelity(tmp_path: Path):
    template_file = tmp_path / "custom_fixture_template.pptx"
    create_fixture_template_with_logo_and_5_layouts(template_file)

    manifest = inspect_template(template_file, "custom_fixture_1", "Custom Fixture Template")
    deck = build_sample_deck(template_id="custom_fixture_1", project_id="p_guard_test")

    out_pptx = tmp_path / "output_deck.pptx"
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_file,
        out_path=out_pptx,
    )

    assert out_pptx.exists()

    # Verify logo image is present in output zip
    with zipfile.ZipFile(str(out_pptx), "r") as z:
        media_files = [n for n in z.namelist() if n.startswith("ppt/media/")]
        assert len(media_files) >= 1, "Template logo must be preserved in rendered output"

    # Verify layout names match
    out_prs = Presentation(str(out_pptx))
    expected_layouts = [
        "Executive Summary Layout",
        "Problem Deep-Dive Layout",
        "Architecture System Layout",
        "Market Traction Layout",
        "Financial Projections Layout",
    ]
    for i, name in enumerate(expected_layouts):
        assert out_prs.slide_layouts[i].name == name

