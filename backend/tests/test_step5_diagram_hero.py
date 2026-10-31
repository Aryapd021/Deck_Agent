import tempfile
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches

from app.models.deck import Slide, Field, Source, SlideType
from app.models.facts import ComponentGraph, Node, Edge, NodeKind
from app.services.diagram import render_diagram, get_palette
from app.services.design import render_architecture_slide, render_demo_slide, Box, ProfileTokens
from app.services.template_inspector import analyze_template


def test_diagram_hero_dimensions_and_isolation(tmp_path: Path) -> None:
    # 1. Create a sample architecture graph
    nodes = [
        Node(id="n1", label="Frontend UI", kind=NodeKind.FRONTEND, tech="React"),
        Node(id="n2", label="Backend API", kind=NodeKind.BACKEND, tech="FastAPI"),
        Node(id="n3", label="Postgres DB", kind=NodeKind.DB, tech="SQLModel"),
        Node(id="n4", label="Worker Engine", kind=NodeKind.AI, tech="Claude"),
    ]
    edges = [
        Edge(from_="n1", to="n2", label="REST"),
        Edge(from_="n2", to="n3", label="SQL"),
        Edge(from_="n2", to="n4", label="Async Job"),
    ]
    graph = ComponentGraph(nodes=nodes, edges=edges)
    palette = get_palette(None)

    svg_path, png_path = render_diagram(
        graph,
        palette,
        tmp_path,
        modern=True,
        target_box_in=(8.0, 4.5),
    )
    assert png_path.exists()

    # 2. Render onto an architecture slide
    prs = Presentation()
    prs.slide_width = 12192000  # 13.333 in
    prs.slide_height = 6858000  # 7.5 in
    slide_layout = prs.slide_layouts[6]  # blank layout
    slide = prs.slides.add_slide(slide_layout)

    # Content box: 12.3 in x 5.4 in
    content_box = Box(
        left=int(0.5 * 914400),
        top=int(1.4 * 914400),
        width=int(12.3 * 914400),
        height=int(5.4 * 914400),
    )
    tmpl_path = tmp_path / "tmpl.pptx"
    prs.save(str(tmpl_path))
    profile = analyze_template(tmpl_path)
    tokens = ProfileTokens.from_profile(profile, box=content_box)

    arch_slide_model = Slide(
        id="arch_slide",
        type=SlideType.ARCHITECTURE,
        title=Field(text="Architecture Overview", source=Source.REPO),
        bullets=[
            Field(text="Clean modular separation of concerns", source=Source.REPO),
            Field(text="High throughput asynchronous processing", source=Source.REPO),
        ],
    )

    render_architecture_slide(slide, arch_slide_model, content_box, tokens, png_path)

    # Verify picture shape on slide
    pictures = [s for s in slide.shapes if s.shape_type == 13]  # 13 = picture
    assert len(pictures) == 1
    pic = pictures[0]

    content_box_area = content_box.width * content_box.height
    pic_area = pic.width * pic.height

    # Hero asserts: area >= 50% of content area, width >= 60% of content box width
    assert pic.width >= int(0.60 * content_box.width), f"Picture width ratio: {pic.width / content_box.width:.2f}"
    assert pic_area >= int(0.50 * content_box_area), f"Picture area ratio: {pic_area / content_box_area:.2f}"

    # 3. Verify demo slide does NOT insert diagram unless explicitly requested
    demo_slide_no_img = prs.slides.add_slide(slide_layout)
    demo_model = Slide(
        id="demo_slide",
        type=SlideType.DEMO,
        title=Field(text="Live Demonstration", source=Source.REPO),
        bullets=[Field(text="Step 1 walkthrough", source=Source.REPO), Field(text="Step 2 results", source=Source.REPO)],
    )
    render_demo_slide(demo_slide_no_img, demo_model, content_box, tokens, png_path)
    demo_pics = [s for s in demo_slide_no_img.shapes if s.shape_type == 13]
    assert len(demo_pics) == 0, "Demo slide must NOT contain diagram if image_ref is unset"
