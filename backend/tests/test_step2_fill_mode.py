import pytest
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches

from app.models.deck import Deck, Slide, Field, Source, SlideType
from app.services.template_inspector import analyze_template, inspect_template
from app.services.render import render_deck


def test_fill_mode_detection_and_slots(tmp_path: Path) -> None:
    # 1. Create a custom template with 4 slides, each having a title and a body placeholder
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Slide 1: Title slide
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "Template Slide 1"

    # Slide 2: Body slide
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Problem Statement"
    s2.placeholders[1].text = "Click to edit problem details"

    # Slide 3: Body slide
    s3 = prs.slides.add_slide(prs.slide_layouts[1])
    s3.shapes.title.text = "Solution Architecture"
    s3.placeholders[1].text = "Click to edit solution details"

    # Slide 4: Metric slide
    s4 = prs.slides.add_slide(prs.slide_layouts[1])
    s4.shapes.title.text = "Impact & Metrics"
    s4.placeholders[1].text = "Click to edit metric numbers"

    tmpl_file = tmp_path / "custom_template.pptx"
    prs.save(str(tmpl_file))

    # 2. Analyze template
    profile = analyze_template(tmpl_file)
    manifest = inspect_template(tmpl_file, "custom_tmpl_id", "Custom 4-Slide Template")

    assert profile.mode == "fill"
    assert len(profile.slots) == 4

    # Verify slot extraction
    slot0 = profile.slots[0]
    assert slot0.slot_index == 0
    assert slot0.role == "title"

    slot3 = profile.slots[3]
    assert slot3.slot_index == 3
    assert slot3.role == "metric" or slot3.role == "body"

    # 3. Render a deck in fill mode and verify output slide count matches template exactly (4 slides)
    deck = Deck(
        schema_version=2,
        project_id="fill_proj",
        version=1,
        template_id="custom_tmpl_id",
        slides=[
            Slide(type=SlideType.TITLE, title=Field(text="Pitch Deck", source=Source.REPO)),
            Slide(type=SlideType.PROBLEM, title=Field(text="Major Problem", source=Source.REPO), bullets=[
                Field(text="High operational latencies degrade customer experience significantly.", source=Source.REPO)
            ]),
            Slide(type=SlideType.SOLUTION, title=Field(text="Modern Solution", source=Source.REPO), bullets=[
                Field(text="Asynchronous streaming architectures decouple frontend from processing bottlenecks.", source=Source.REPO)
            ]),
            Slide(type=SlideType.IMPACT, title=Field(text="Measurable Impact", source=Source.REPO), bullets=[
                Field(text="Achieved 4x latency reduction and 99.99% system availability.", source=Source.REPO)
            ]),
        ],
    )

    out_file = tmp_path / "out_fill.pptx"
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=tmpl_file,
        out_path=out_file,
    )

    assert out_file.exists()
    out_prs = Presentation(str(out_file))
    assert len(out_prs.slides) == 4, f"Fill mode output must have 4 slides, got {len(out_prs.slides)}"
