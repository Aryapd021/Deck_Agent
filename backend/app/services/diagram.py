import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.models.facts import ComponentGraph, Node, NodeKind

DEFAULT_PALETTE: dict[str, str] = {
    "background": "#FFFFFF",
    "text": "#1F2937",
    "primary": "#2563EB",
    "secondary": "#7C3AED",
    "accent": "#059669",
}

KIND_STYLES: dict[str, tuple[str, str]] = {
    "frontend": ("box", "rounded,filled"),
    "backend": ("box", "filled"),
    "db": ("cylinder", "filled"),
    "ai": ("hexagon", "filled"),
    "external": ("ellipse", "dashed,filled"),
    "infra": ("folder", "filled"),
}

KIND_PALETTE_KEYS: dict[str, str] = {
    "frontend": "primary",
    "backend": "secondary",
    "db": "accent",
    "ai": "primary",
    "external": "secondary",
    "infra": "accent",
}


def get_palette(manifest: Any) -> dict[str, str]:
    """Merges manifest.palette over DEFAULT_PALETTE (missing keys fall back)."""
    merged = dict(DEFAULT_PALETTE)
    if manifest is None:
        return merged

    palette_data = (
        manifest.get("palette")
        if isinstance(manifest, dict)
        else getattr(manifest, "palette", None)
    )

    if isinstance(palette_data, dict):
        for k, v in palette_data.items():
            if v and isinstance(v, str):
                merged[k] = v

    return merged


def get_font_color(hex_color: str) -> str:
    """Choose black or white font color by perceptual luminance."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 6:
        try:
            r = int(cleaned[0:2], 16) / 255.0
            g = int(cleaned[2:4], 16) / 255.0
            b = int(cleaned[4:6], 16) / 255.0
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            return "#000000" if lum > 0.55 else "#FFFFFF"
        except ValueError:
            pass
    return "#FFFFFF"


def _escape_dot_label(text: str) -> str:
    """Escapes quotes, backslashes, and newlines for DOT format labels."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    return escaped


def _format_node(dot_id: str, node: Node, palette: dict[str, str]) -> str:
    kind_str = node.kind.value if hasattr(node.kind, "value") else str(node.kind).lower()
    shape, style = KIND_STYLES.get(kind_str, ("box", "filled"))
    color_key = KIND_PALETTE_KEYS.get(kind_str, "primary")
    fillcolor = palette.get(color_key, DEFAULT_PALETTE.get(color_key, "#2563EB"))
    fontcolor = get_font_color(fillcolor)
    escaped_label = _escape_dot_label(node.label)
    return (
        f'{dot_id} [label="{escaped_label}", shape="{shape}", style="{style}", '
        f'fillcolor="{fillcolor}", fontcolor="{fontcolor}"];'
    )


def build_dot(graph: ComponentGraph, palette: dict[str, str]) -> str:
    """Builds Graphviz DOT string for the component graph using the given palette."""
    bg_color = palette.get("background", DEFAULT_PALETTE["background"])
    text_color = palette.get("text", DEFAULT_PALETTE["text"])
    accent_color = palette.get("accent", DEFAULT_PALETTE["accent"])

    lines: list[str] = [
        "digraph G {",
        "    rankdir=LR;",
        "    dpi=200;",
        "    pad=0.3;",
        "    nodesep=0.5;",
        "    ranksep=0.8;",
        f'    bgcolor="{bg_color}";',
        '    node [fontname="Helvetica", fontsize=10];',
        f'    edge [fontname="Helvetica", fontsize=9, color="{text_color}"];',
    ]

    id_to_dot: dict[str, str] = {node.id: f"n{i}" for i, node in enumerate(graph.nodes)}

    groups: dict[str, list[tuple[str, Node]]] = {}
    ungrouped: list[tuple[str, Node]] = []

    for i, node in enumerate(graph.nodes):
        dot_id = f"n{i}"
        group_name = (node.group or "").strip()
        if group_name:
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append((dot_id, node))
        else:
            ungrouped.append((dot_id, node))

    # Render groups as cluster subgraphs
    for c_idx, (group_name, members) in enumerate(groups.items()):
        lines.append(f"    subgraph cluster_{c_idx} {{")
        escaped_group = _escape_dot_label(group_name)
        lines.append(f'        label="{escaped_group}";')
        lines.append('        style="rounded,dashed";')
        lines.append(f'        color="{accent_color}";')
        lines.append(f'        fontcolor="{text_color}";')
        for dot_id, node in members:
            lines.append(f"        {_format_node(dot_id, node, palette)}")
        lines.append("    }")

    # Render ungrouped nodes
    for dot_id, node in ungrouped:
        lines.append(f"    {_format_node(dot_id, node, palette)}")

    # Render edges
    for edge in graph.edges:
        from_dot = id_to_dot.get(edge.from_)
        to_dot = id_to_dot.get(edge.to)
        if from_dot and to_dot:
            if edge.label and edge.label.strip():
                escaped_edge = _escape_dot_label(edge.label.strip())
                lines.append(f'    {from_dot} -> {to_dot} [label="{escaped_edge}"];')
            else:
                lines.append(f"    {from_dot} -> {to_dot};")

    lines.append("}")
    return "\n".join(lines)


def render_diagram(
    graph: ComponentGraph,
    palette: dict[str, str],
    out_dir: Path,
) -> tuple[Path, Path]:
    """Renders diagram.svg and diagram.png into out_dir using dot."""
    dot_binary = shutil.which("dot")
    if dot_binary is None:
        local_dot = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "Graphviz"
            / "Graphviz-15.1.1-win64"
            / "bin"
            / "dot.exe"
        )
        if local_dot.exists():
            dot_binary = str(local_dot)
        else:
            raise ApiError(ErrorCode.TOOL_MISSING, "Graphviz 'dot' binary is not installed or not in PATH")

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    svg_path = out_path / "diagram.svg"
    png_path = out_path / "diagram.png"

    dot_content = build_dot(graph, palette)

    # Render SVG
    try:
        proc_svg = subprocess.run(
            [dot_binary, "-Tsvg"],
            input=dot_content,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        raise RuntimeError(f"dot -Tsvg execution error: {e}") from e

    if proc_svg.returncode != 0:
        raise RuntimeError(
            f"dot -Tsvg failed with exit code {proc_svg.returncode}: {proc_svg.stderr.strip()}"
        )
    svg_path.write_text(proc_svg.stdout, encoding="utf-8")

    # Render PNG
    try:
        proc_png = subprocess.run(
            [dot_binary, "-Tpng"],
            input=dot_content.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        raise RuntimeError(f"dot -Tpng execution error: {e}") from e

    if proc_png.returncode != 0:
        stderr_text = proc_png.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"dot -Tpng failed with exit code {proc_png.returncode}: {stderr_text}"
        )
    png_path.write_bytes(proc_png.stdout)

    return svg_path, png_path
