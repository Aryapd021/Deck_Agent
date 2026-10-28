import os
from pathlib import Path
import shutil
import unittest.mock as mock

import pytest

from app.core.errors import ApiError, ErrorCode
from app.models.api import TemplateManifest
from app.models.facts import ComponentGraph, Edge, Node, NodeKind
from app.services.diagram import (
    DEFAULT_PALETTE,
    build_dot,
    get_font_color,
    get_palette,
    render_diagram,
)
from app.services.template_inspector import extract_palette


def _has_dot() -> bool:
    if shutil.which("dot") is not None:
        return True
    local_dot = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Graphviz"
        / "Graphviz-15.1.1-win64"
        / "bin"
        / "dot.exe"
    )
    return local_dot.exists()


@pytest.fixture
def sample_5_node_graph() -> ComponentGraph:
    return ComponentGraph(
        nodes=[
            Node(
                id="raw_frontend_id",
                label='React "Dashboard"\nv2.0',
                kind=NodeKind.FRONTEND,
                group="Presentation",
            ),
            Node(
                id="raw_backend_id",
                label="API Gateway\nFastAPI",
                kind=NodeKind.BACKEND,
                group="Presentation",
            ),
            Node(
                id="raw_db_id",
                label="Postgres DB",
                kind=NodeKind.DB,
                group=None,
            ),
            Node(
                id="raw_ai_id",
                label="Gemini Agent",
                kind=NodeKind.AI,
                group="Intelligence",
            ),
            Node(
                id="raw_ext_id",
                label='Third-Party "Auth"',
                kind=NodeKind.EXTERNAL,
                group=None,
            ),
        ],
        edges=[
            Edge(from_="raw_frontend_id", to="raw_backend_id", label="HTTPS"),
            Edge(from_="raw_backend_id", to="raw_db_id", label="SQL"),
            Edge(from_="raw_backend_id", to="raw_ai_id", label="gRPC"),
            Edge(from_="raw_backend_id", to="raw_ext_id", label="OAuth"),
        ],
    )


def test_build_dot_5_nodes_structure_and_escaping(sample_5_node_graph: ComponentGraph) -> None:
    dot_output = build_dot(sample_5_node_graph, DEFAULT_PALETTE)

    # 1. Graph attribute assertions
    assert "rankdir=LR;" in dot_output
    assert "dpi=200;" in dot_output
    assert "pad=0.3;" in dot_output
    assert "nodesep=0.5;" in dot_output
    assert "ranksep=0.8;" in dot_output
    assert f'bgcolor="{DEFAULT_PALETTE["background"]}";' in dot_output

    # 2. Node IDs sanitized to n0..n4
    for i in range(5):
        assert f"n{i} [" in dot_output
    assert "raw_frontend_id [" not in dot_output
    assert "raw_backend_id [" not in dot_output
    assert "raw_db_id [" not in dot_output
    assert "raw_ai_id [" not in dot_output
    assert "raw_ext_id [" not in dot_output

    # 3. Escaping quotes and newlines in labels
    assert r'React \"Dashboard\"\nv2.0' in dot_output
    assert r'Third-Party \"Auth\"' in dot_output

    # 4. Group cluster subgraphs
    assert "subgraph cluster_0 {" in dot_output
    assert 'label="Presentation";' in dot_output
    assert "subgraph cluster_1 {" in dot_output
    assert 'label="Intelligence";' in dot_output

    # 5. Node shapes by kind
    assert 'shape="box", style="rounded,filled"' in dot_output  # frontend
    assert 'shape="box", style="filled"' in dot_output          # backend
    assert 'shape="cylinder", style="filled"' in dot_output     # db
    assert 'shape="hexagon", style="filled"' in dot_output      # ai
    assert 'shape="ellipse", style="dashed,filled"' in dot_output  # external

    # 6. Edges formatted
    assert 'n0 -> n1 [label="HTTPS"];' in dot_output
    assert 'n1 -> n2 [label="SQL"];' in dot_output


def test_palette_merge() -> None:
    # None manifest falls back to defaults
    p1 = get_palette(None)
    assert p1 == DEFAULT_PALETTE

    # Manifest with None palette falls back to defaults
    manifest_mock = mock.Mock()
    manifest_mock.palette = None
    p2 = get_palette(manifest_mock)
    assert p2 == DEFAULT_PALETTE

    # Manifest with partial palette overrides only given keys
    manifest_mock.palette = {"primary": "#AABBCC"}
    p3 = get_palette(manifest_mock)
    assert p3["primary"] == "#AABBCC"
    assert p3["background"] == DEFAULT_PALETTE["background"]
    assert p3["secondary"] == DEFAULT_PALETTE["secondary"]
    assert p3["accent"] == DEFAULT_PALETTE["accent"]

    # Dict input format
    p4 = get_palette({"palette": {"accent": "#998877"}})
    assert p4["accent"] == "#998877"
    assert p4["primary"] == DEFAULT_PALETTE["primary"]


def test_font_color_luminance() -> None:
    # Light backgrounds get dark font
    assert get_font_color("#FFFFFF") == "#000000"
    assert get_font_color("#EEECE1") == "#000000"
    # Dark backgrounds get white font
    assert get_font_color("#000000") == "#FFFFFF"
    assert get_font_color("#2563EB") == "#FFFFFF"
    assert get_font_color("#7C3AED") == "#FFFFFF"


def test_extract_palette_real_template() -> None:
    template_path = Path("templates/_source/NeuroClear_SIH_2026_Refined.pptx")
    if not template_path.exists():
        pytest.skip("Sample template not found")

    palette = extract_palette(template_path)
    assert isinstance(palette, dict)
    assert len(palette) >= 5
    for key in ("background", "text", "primary", "secondary", "accent"):
        assert key in palette
        assert palette[key].startswith("#")
        assert len(palette[key]) == 7

    # Failure returns empty dict
    assert extract_palette("non_existent_file.pptx") == {}


@pytest.mark.skipif(not _has_dot(), reason="Graphviz dot binary is not installed")
def test_render_diagram_success(sample_5_node_graph: ComponentGraph, tmp_path: Path) -> None:
    svg_path, png_path = render_diagram(sample_5_node_graph, DEFAULT_PALETTE, tmp_path)

    assert svg_path.exists()
    assert svg_path.name == "diagram.svg"
    assert svg_path.stat().st_size > 0
    svg_content = svg_path.read_text(encoding="utf-8")
    assert "<svg" in svg_content

    assert png_path.exists()
    assert png_path.name == "diagram.png"
    assert png_path.stat().st_size > 0
    png_bytes = png_path.read_bytes()
    # PNG file magic bytes
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_diagram_tool_missing(sample_5_node_graph: ComponentGraph, tmp_path: Path) -> None:
    with mock.patch("shutil.which", return_value=None), mock.patch(
        "pathlib.Path.exists", return_value=False
    ):
        with pytest.raises(ApiError) as exc_info:
            render_diagram(sample_5_node_graph, DEFAULT_PALETTE, tmp_path)
        assert exc_info.value.code == ErrorCode.TOOL_MISSING


def test_render_diagram_non_zero_exit(
    sample_5_node_graph: ComponentGraph, tmp_path: Path
) -> None:
    fake_proc = mock.Mock()
    fake_proc.returncode = 1
    fake_proc.stderr = "Error: syntax error in line 42"

    with mock.patch("shutil.which", return_value="dot"), mock.patch(
        "subprocess.run", return_value=fake_proc
    ):
        with pytest.raises(RuntimeError) as exc_info:
            render_diagram(sample_5_node_graph, DEFAULT_PALETTE, tmp_path)
        assert "syntax error in line 42" in str(exc_info.value)
