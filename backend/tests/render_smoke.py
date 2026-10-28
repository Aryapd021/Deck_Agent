import shutil
import sys
from pathlib import Path

from app.core.storage import (
    finalize_version_dir,
    new_temp_version_dir,
    project_dir,
    template_pptx_path,
)
from app.services.diagram import get_palette, render_diagram
from app.services.render import render_deck
from app.services.template_inspector import load_manifest
from tests.fixtures.sample_deck import build_sample_deck


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m tests.render_smoke <template_id>", file=sys.stderr)
        sys.exit(1)

    template_id = sys.argv[1]
    project_id = "_smoke"

    p_dir = project_dir(project_id)
    if p_dir.exists():
        shutil.rmtree(p_dir)

    manifest = load_manifest(template_id)
    deck = build_sample_deck(template_id=template_id, project_id=project_id)

    tmp_dir = new_temp_version_dir(project_id, 1)
    template_path = template_pptx_path(template_id)
    out_pptx = tmp_dir / "deck.pptx"

    diagram_png_path = None
    if deck.diagram is not None:
        palette = get_palette(manifest)
        _, diagram_png_path = render_diagram(deck.diagram, palette, tmp_dir)

    render_deck(
        deck=deck,
        manifest=manifest,
        template_path=template_path,
        out_path=out_pptx,
        diagram_png=diagram_png_path,
    )

    deck_json_path = tmp_dir / "deck.json"
    deck_json_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")

    final_dir = finalize_version_dir(tmp_dir, project_id, 1)
    final_pptx = final_dir / "deck.pptx"
    print(str(final_pptx))


if __name__ == "__main__":
    main()
