import pytest
from pptx import Presentation
from pathlib import Path

from app.models.deck import Slide, Field, Source, SlideType
from app.services.design import (
    Box,
    ProfileTokens,
    place_grid,
    compute_slide_bands,
    get_real_pill_label,
    ensure_titles_on_top,
    render_title_slide,
    render_features_slide,
    render_designed_slide,
)
from app.services.template_inspector import analyze_template


def test_12_column_grid_snapping() -> None:
    content_box = Box(left=500000, top=1000000, width=12000000, height=5000000)

    # 1 card: spans 12 cols, height is capped (never spans full 5.5 inches)
    b1 = place_grid(1, content_box)
    assert len(b1) == 1
    assert b1[0].width == content_box.width
    assert b1[0].height < content_box.height

    # 2 cards: 6 cols each
    b2 = place_grid(2, content_box)
    assert len(b2) == 2
    assert abs(b2[0].width - b2[1].width) <= 20
    assert b2[0].right < b2[1].left  # Gutter between them

    # 3 cards: 4 cols each
    b3 = place_grid(3, content_box)
    assert len(b3) == 3
    assert abs(b3[0].width - b3[1].width) <= 20
    assert abs(b3[1].width - b3[2].width) <= 20

    # 4 cards: horizontal mode -> 3 cols each
    b4_h = place_grid(4, content_box, policy="horizontal")
    assert len(b4_h) == 4
    assert abs(b4_h[0].width - b4_h[1].width) <= 20

    # 4 cards: auto mode in standard 16:9 (2x2) -> 6 cols each
    b4_grid = place_grid(4, content_box, policy="grid")
    assert len(b4_grid) == 4
    assert b4_grid[0].top == b4_grid[1].top
    assert b4_grid[2].top == b4_grid[3].top
    assert b4_grid[2].top > b4_grid[0].top


def test_real_pill_labels() -> None:
    # 1. From explicit heading if available
    class HeadingItem:
        heading = "Redis Cache"
        text = "Fast in-memory cache for speed"
    assert get_real_pill_label(HeadingItem(), "features") == "REDIS CACHE"

    # 2. From leading colon
    f_colon = Field(text="Distributed Engine: Processes petabyte scale logs", source=Source.REPO)
    assert get_real_pill_label(f_colon, "features") == "DISTRIBUTED ENGINE"

    # 3. From tech keyword
    f_tech = Field(text="High-throughput FastAPI gateway handling requests", source=Source.REPO)
    assert get_real_pill_label(f_tech, "tech_stack") == "FASTAPI"

    # 4. Never generic
    f_generic = Field(text="The system optimizes query performance automatically", source=Source.REPO)
    label = get_real_pill_label(f_generic, "features")
    assert "FEATURE" not in label
    assert "STACK" not in label


def test_z_order_title_protection(tmp_path: Path) -> None:
    prs = Presentation()
    prs.slide_width = 12192000
    prs.slide_height = 6858000
    slide = prs.slides.add_slide(prs.slide_layouts[0])

    # Ensure title placeholder exists
    title_ph = slide.shapes.title
    title_ph.text = "Antigravity Pitch"

    tmpl_path = tmp_path / "tmpl.pptx"
    prs.save(str(tmpl_path))
    profile = analyze_template(tmpl_path)
    content_box = Box(500000, 1000000, 11000000, 5000000)
    tokens = ProfileTokens.from_profile(profile, box=content_box)

    slide_model = Slide(
        id="title_slide",
        type=SlideType.TITLE,
        title=Field(text="Antigravity Pitch", source=Source.REPO),
        subtitle=Field(text="Autonomous coding engine", source=Source.REPO),
    )

    # Render designed title slide
    render_title_slide(slide, slide_model, content_box, tokens, is_custom=False)
    ensure_titles_on_top(slide)

    # Inspect spTree order: title shape must be in front of left_panel and right_bg
    spTree = slide.shapes._spTree
    title_idx = spTree.index(title_ph._element)

    # All rectangle panels must be behind the title in document order
    for shape in slide.shapes:
        if shape != title_ph and not (shape.is_placeholder and shape.placeholder_format.idx == 1):
            if "panel" in shape.name.lower() or "rectangle" in shape.name.lower():
                idx = spTree.index(shape._element)
                assert idx < title_idx, f"Shape {shape.name} at {idx} is on top of title at {title_idx}"
