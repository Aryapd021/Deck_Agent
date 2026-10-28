from pathlib import Path
import struct

from pptx import Presentation
from pptx.parts.image import Image

from app.models.api import TemplateManifest
from app.models.deck import Deck
from app.services.template_inspector import resolve_picture_target


def _get_png_dimensions(png_path: Path) -> tuple[int, int]:
    try:
        with open(png_path, "rb") as f:
            header = f.read(24)
            if (
                len(header) >= 24
                and header[:8] == b"\x89PNG\r\n\x1a\n"
                and header[12:16] == b"IHDR"
            ):
                w, h = struct.unpack(">II", header[16:24])
                if w > 0 and h > 0:
                    return w, h
    except Exception:
        pass
    img = Image.from_file(str(png_path))
    return img.size


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

            # Image insertion for diagram
            if slide.image_ref == "diagram" and diagram_png is not None:
                diagram_file = Path(diagram_png)
                if diagram_file.exists() and diagram_file.is_file():
                    target = resolve_picture_target(manifest, st_key)
                    if target is not None:
                        img_w, img_h = _get_png_dimensions(diagram_file)
                        if img_w > 0 and img_h > 0:
                            if target.kind == "placeholder":
                                box_left = target.left
                                box_top = target.top
                                box_width = target.width
                                box_height = target.height

                                scale = min(box_width / img_w, box_height / img_h)
                                pic_width = int(img_w * scale)
                                pic_height = int(img_h * scale)
                                pic_left = box_left + int((box_width - pic_width) / 2)
                                pic_top = box_top + int((box_height - pic_height) / 2)

                                pptx_slide.shapes.add_picture(
                                    str(diagram_file),
                                    pic_left,
                                    pic_top,
                                    pic_width,
                                    pic_height,
                                )

                                # Remove the empty picture placeholder element
                                if target.idx is not None and target.idx in ph_by_idx:
                                    pic_ph = ph_by_idx[target.idx]
                                    sp = pic_ph._element
                                    parent = sp.getparent()
                                    if parent is not None:
                                        parent.remove(sp)

                            elif target.kind == "area":
                                if slide.bullets:
                                    body_ph = (
                                        ph_by_idx.get(slot.body_idx)
                                        if slot and slot.body_idx is not None
                                        else None
                                    )
                                    orig_left = body_ph.left if body_ph else target.left
                                    orig_top = body_ph.top if body_ph else target.top
                                    orig_width = body_ph.width if body_ph else target.width
                                    orig_height = body_ph.height if body_ph else target.height

                                    new_body_width = int(orig_width * 0.35)
                                    if body_ph:
                                        body_ph.width = new_body_width

                                    rem_left = orig_left + new_body_width
                                    rem_width = orig_width - new_body_width
                                    rem_top = orig_top
                                    rem_height = orig_height

                                    scale = min(rem_width / img_w, rem_height / img_h)
                                    pic_width = int(img_w * scale)
                                    pic_height = int(img_h * scale)
                                    pic_left = rem_left + int((rem_width - pic_width) / 2)
                                    pic_top = rem_top + int((rem_height - pic_height) / 2)

                                    pptx_slide.shapes.add_picture(
                                        str(diagram_file),
                                        pic_left,
                                        pic_top,
                                        pic_width,
                                        pic_height,
                                    )
                                else:
                                    box_left = target.left
                                    box_top = target.top
                                    box_width = target.width
                                    box_height = target.height

                                    scale = min(box_width / img_w, box_height / img_h)
                                    pic_width = int(img_w * scale)
                                    pic_height = int(img_h * scale)
                                    pic_left = box_left + int((box_width - pic_width) / 2)
                                    pic_top = box_top + int((box_height - pic_height) / 2)

                                    pptx_slide.shapes.add_picture(
                                        str(diagram_file),
                                        pic_left,
                                        pic_top,
                                        pic_width,
                                        pic_height,
                                    )

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
