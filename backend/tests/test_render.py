from pathlib import Path

import pytest
from pptx import Presentation

from app.services.render import render_deck
from app.services.template_inspector import inspect_template
from tests.fixtures.sample_deck import build_sample_deck


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
