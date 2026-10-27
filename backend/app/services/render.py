from pathlib import Path

from pptx import Presentation

from app.models.api import TemplateManifest
from app.models.deck import Deck


def render_deck(
    deck: Deck,
    manifest: TemplateManifest,
    template_path: Path,
    out_path: Path,
    diagram_png: Path | None = None,
) -> None:
    template_file = Path(template_path)
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation(str(template_file))

    # Delete every existing slide properly to prevent PowerPoint repair prompts
    for sldId in list(prs.slides._sldIdLst):
        prs.part.drop_rel(sldId.rId)
        prs.slides._sldIdLst.remove(sldId)

    for slide in deck.slides:
        st_key = slide.type.value
        layout_idx = manifest.layout_map[st_key]
        slide_layout = prs.slide_layouts[layout_idx]
        pptx_slide = prs.slides.add_slide(slide_layout)

        slot = manifest.slots.get(st_key)
        ph_by_idx = {ph.placeholder_format.idx: ph for ph in pptx_slide.placeholders}

        if slot:
            # Title text into title_idx
            if slot.title_idx is not None and slot.title_idx in ph_by_idx:
                title_ph = ph_by_idx[slot.title_idx]
                if title_ph.has_text_frame:
                    title_ph.text_frame.text = slide.title.text

            # Subtitle into subtitle_idx if present and mapped
            if (
                slide.subtitle is not None
                and slot.subtitle_idx is not None
                and slot.subtitle_idx in ph_by_idx
            ):
                sub_ph = ph_by_idx[slot.subtitle_idx]
                if sub_ph.has_text_frame:
                    sub_ph.text_frame.text = slide.subtitle.text

            # Bullets into body_idx
            if (
                slot.body_idx is not None
                and slot.body_idx in ph_by_idx
                and slide.bullets
            ):
                body_ph = ph_by_idx[slot.body_idx]
                if body_ph.has_text_frame:
                    tf = body_ph.text_frame
                    tf.paragraphs[0].text = slide.bullets[0].text
                    tf.paragraphs[0].level = 0
                    for bullet in slide.bullets[1:]:
                        p = tf.add_paragraph()
                        p.text = bullet.text
                        p.level = 0

            # Image insertion is Day 3 (when image_ref == "diagram" do nothing yet)
            if slide.image_ref == "diagram" and diagram_png is not None:
                pass

        # Speaker notes via notes_slide
        if slide.notes:
            pptx_slide.notes_slide.notes_text_frame.text = slide.notes

        # Remove every still-empty placeholder element so no "Click to add text" boxes remain
        for ph in list(pptx_slide.placeholders):
            is_empty = False
            if ph.has_text_frame:
                if not ph.text_frame.text.strip():
                    is_empty = True
            else:
                is_empty = True

            if is_empty:
                sp = ph._element
                parent = sp.getparent()
                if parent is not None:
                    parent.remove(sp)

    prs.save(str(out_file))
