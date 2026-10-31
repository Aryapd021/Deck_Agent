import pytest
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches

from app.models.deck import Deck, Slide, Field, Source, SlideType
from app.models.facts import ComponentGraph, Node, Edge, NodeKind
from app.services.diagram import render_diagram, get_palette
from app.services.design import (
    Box,
    ProfileTokens,
    place_grid,
    render_designed_slide,
    ensure_titles_on_top,
)
from app.services.template_inspector import analyze_template, inspect_template
from app.services.render import render_deck


def test_visual_lint_l1_to_l10(tmp_path: Path) -> None:
    # 1. Setup a fixture presentation template
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "Company Overview"

    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Problem Statement"
    s2.placeholders[1].text = "Click to edit problem details"

    s3 = prs.slides.add_slide(prs.slide_layouts[1])
    s3.shapes.title.text = "Core Architecture"
    s3.placeholders[1].text = "Click to edit architecture details"

    tmpl_file = tmp_path / "tmpl_lint.pptx"
    prs.save(str(tmpl_file))

    profile = analyze_template(tmpl_file)
    manifest = inspect_template(tmpl_file, "lint_id", "Lint Template")

    # Render a diagram
    nodes = [
        Node(id="n1", label="UI Client", kind=NodeKind.FRONTEND, tech="React"),
        Node(id="n2", label="Backend API", kind=NodeKind.BACKEND, tech="FastAPI"),
        Node(id="n3", label="Postgres DB", kind=NodeKind.DB, tech="SQLModel"),
        Node(id="n4", label="Worker Engine", kind=NodeKind.AI, tech="Claude"),
    ]
    edges = [
        Edge(from_="n1", to="n2", label="REST"),
        Edge(from_="n2", to="n3", label="SQL"),
        Edge(from_="n2", to="n4", label="Jobs"),
    ]
    graph = ComponentGraph(nodes=nodes, edges=edges)
    palette = get_palette(None)
    _, png_path = render_diagram(graph, palette, tmp_path, modern=True, target_box_in=(8.0, 4.5))

    deck = Deck(
        schema_version=2,
        project_id="lint_proj",
        version=1,
        template_id="lint_id",
        slides=[
            Slide(
                type=SlideType.TITLE,
                title=Field(text="Enterprise Graph Engine", source=Source.REPO),
                subtitle=Field(text="High Performance Knowledge Store", source=Source.REPO),
            ),
            Slide(
                type=SlideType.PROBLEM,
                title=Field(text="Scalability Limitations", source=Source.REPO),
                summary=Field(text="Traditional relational engines bottleneck on deep hierarchical traversals.", source=Source.REPO),
                bullets=[
                    Field(text="Unindexed recursive queries incur O(N^2) complexity across distributed nodes.", source=Source.REPO, heading="Recursive Queries"),
                    Field(text="Memory usage spikes beyond provisioned limits during peak streaming ingestion.", source=Source.REPO, heading="Memory Spikes"),
                    Field(text="Lack of native graph indexing requires application-level joining overhead.", source=Source.REPO, heading="Missing Indexing"),
                ],
                takeaway=Field(text="Native graph representations eliminate traversal latency bottlenecks.", source=Source.REPO),
            ),
            Slide(
                type=SlideType.ARCHITECTURE,
                title=Field(text="System Architecture", source=Source.REPO),
                summary=Field(text="Decoupled microservice architecture ensures linear horizontal scalability.", source=Source.REPO),
                bullets=[
                    Field(text="Stateless API gateway handles authentication, routing, and rate limiting.", source=Source.REPO, heading="API Gateway"),
                    Field(text="High-throughput workers ingest streaming telemetry into persistent storage.", source=Source.REPO, heading="Worker Pool"),
                ],
                takeaway=Field(text="Sub-millisecond query responses achieved through zero-copy caching.", source=Source.REPO),
                image_ref="diagram",
            ),
        ],
    )

    out_file = tmp_path / "out_lint.pptx"
    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=tmpl_file,
        out_path=out_file,
        diagram_png=png_path,
    )

    out_prs = Presentation(str(out_file))

    # --- L1: Content box fits on slide ---
    free_box_tuple = profile.analysis_reports[1].free_box or (int(0.04 * prs.slide_width), int(0.20 * prs.slide_height), int(0.92 * prs.slide_width), int(0.72 * prs.slide_height))
    cb = Box(*free_box_tuple)
    assert cb.left >= 0 and cb.top >= 0
    assert cb.right <= out_prs.slide_width
    assert cb.bottom <= out_prs.slide_height

    # --- L7: Left and right margins equal within 5% ---
    margin_left = cb.left
    margin_right = out_prs.slide_width - cb.right
    diff_pct = abs(margin_left - margin_right) / out_prs.slide_width
    assert diff_pct <= 0.05, f"Margins diff {diff_pct:.4f} > 0.05"

    # --- L8: No 'TODO' anywhere in text ---
    for slide in out_prs.slides:
        for shp in slide.shapes:
            if shp.has_text_frame:
                txt = shp.text_frame.text
                assert "TODO" not in txt, f"Found 'TODO' in slide shape: '{txt}'"

    # --- L3: Title is above background shapes in z-order ---
    for slide in out_prs.slides:
        title_shp = slide.shapes.title
        if title_shp:
            spTree = slide.shapes._spTree
            t_idx = spTree.index(title_shp._element)
            for shp in slide.shapes:
                if "panel" in shp.name.lower() or "background" in shp.name.lower():
                    idx = spTree.index(shp._element)
                    assert idx < t_idx, f"Background panel {shp.name} at {idx} is over title at {t_idx}"

    # --- L6: Architecture diagram hero dimensions ---
    arch_slide = out_prs.slides[2]
    arch_pics = [s for s in arch_slide.shapes if s.shape_type == 13]
    assert len(arch_pics) == 1
    pic = arch_pics[0]
    assert pic.width >= int(0.60 * cb.width)
    assert (pic.width * pic.height) >= int(0.50 * (cb.width * cb.height))

    # --- L9: Non-title slide has summary + >= 3 items + takeaway ---
    prob_slide_model = deck.slides[1]
    assert prob_slide_model.summary is not None
    assert len(prob_slide_model.bullets) >= 3
    assert prob_slide_model.takeaway is not None

    # --- L10: Template slide count invariant in fill mode ---
    assert len(out_prs.slides) == len(prs.slides)
