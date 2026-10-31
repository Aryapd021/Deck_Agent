from pathlib import Path
import struct

from pptx import Presentation
from pptx.parts.image import Image

from pptx.enum.dml import MSO_COLOR_TYPE
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.util import Inches, Pt

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


def _extract_font_style(shape_or_p, default_size=Pt(13)) -> dict:
    style = {
        "name": None,
        "size": default_size,
        "bold": None,
        "color_type": None,
        "color_rgb": None,
        "color_theme": None,
    }
    if not shape_or_p:
        return style

    # If it's a shape with a text frame
    if hasattr(shape_or_p, "text_frame"):
        tf = shape_or_p.text_frame
        for p in tf.paragraphs:
            for r in p.runs:
                if not style["name"] and r.font.name:
                    style["name"] = r.font.name
                if style["size"] == default_size and r.font.size:
                    style["size"] = r.font.size
                if style["bold"] is None and r.font.bold is not None:
                    style["bold"] = r.font.bold
                if not style["color_type"] and r.font.color and getattr(r.font.color, "type", None):
                    c_type = r.font.color.type
                    style["color_type"] = c_type
                    if c_type == MSO_COLOR_TYPE.RGB:
                        style["color_rgb"] = r.font.color.rgb
                    elif c_type == MSO_COLOR_TYPE.SCHEME:
                        try:
                            style["color_theme"] = r.font.color.theme_color
                        except Exception:
                            pass
            if not style["name"] and p.font.name:
                style["name"] = p.font.name
            if style["size"] == default_size and p.font.size:
                style["size"] = p.font.size
            if not style["color_type"] and p.font.color and getattr(p.font.color, "type", None):
                c_type = p.font.color.type
                style["color_type"] = c_type
                if c_type == MSO_COLOR_TYPE.RGB:
                    style["color_rgb"] = p.font.color.rgb
                elif c_type == MSO_COLOR_TYPE.SCHEME:
                    try:
                        style["color_theme"] = p.font.color.theme_color
                    except Exception:
                        pass
    # If it's a paragraph
    elif hasattr(shape_or_p, "runs"):
        p = shape_or_p
        for r in p.runs:
            if not style["name"] and r.font.name:
                style["name"] = r.font.name
            if style["size"] == default_size and r.font.size:
                style["size"] = r.font.size
            if style["bold"] is None and r.font.bold is not None:
                style["bold"] = r.font.bold
            if not style["color_type"] and r.font.color and getattr(r.font.color, "type", None):
                c_type = r.font.color.type
                style["color_type"] = c_type
                if c_type == MSO_COLOR_TYPE.RGB:
                    style["color_rgb"] = r.font.color.rgb
                elif c_type == MSO_COLOR_TYPE.SCHEME:
                    try:
                        style["color_theme"] = r.font.color.theme_color
                    except Exception:
                        pass
        if not style["name"] and p.font.name:
            style["name"] = p.font.name
        if style["size"] == default_size and p.font.size:
            style["size"] = p.font.size
        if not style["color_type"] and p.font.color and getattr(p.font.color, "type", None):
            c_type = p.font.color.type
            style["color_type"] = c_type
            if c_type == MSO_COLOR_TYPE.RGB:
                style["color_rgb"] = p.font.color.rgb
            elif c_type == MSO_COLOR_TYPE.SCHEME:
                try:
                    style["color_theme"] = p.font.color.theme_color
                except Exception:
                    pass

    return style


def _apply_font_style(target_p, style: dict) -> None:
    try:
        font = target_p.font
        if style.get("name"):
            font.name = style["name"]
        if style.get("size"):
            font.size = style["size"]
        if style.get("bold") is not None:
            font.bold = style["bold"]
        if style.get("color_type") == MSO_COLOR_TYPE.RGB and style.get("color_rgb"):
            font.color.rgb = style["color_rgb"]
        elif style.get("color_type") == MSO_COLOR_TYPE.SCHEME and style.get("color_theme") is not None:
            font.color.theme_color = style["color_theme"]
    except Exception:
        pass


def _copy_font_style(source_p, target_p, default_size=Pt(13)) -> None:
    style = _extract_font_style(source_p, default_size)
    _apply_font_style(target_p, style)


def _render_deck_into_existing_slides(
    prs: Presentation,
    deck: Deck,
    manifest: TemplateManifest,
    diagram_png: Path | None = None,
) -> None:
    # 1. Clean up instruction / guideline slides if present at the end
    if len(prs.slides) > 1:
        to_remove = []
        for sld_idx, sld in enumerate(prs.slides):
            all_txt = " ".join(
                [sh.text_frame.text for sh in sld.shapes if sh.has_text_frame]
            ).upper()
            if any(
                k in all_txt
                for k in [
                    "IMPORTANT INSTRUCTIONS",
                    "IMPORTANT POINTERS",
                    "DELETE THIS SLIDE",
                    "KINDLY KEEP THE MAXIMUM SLIDES",
                ]
            ):
                to_remove.append(sld_idx)
        for sld_idx in reversed(to_remove):
            sldId = list(prs.slides._sldIdLst)[sld_idx]
            prs.part.drop_rel(sldId.rId)
            prs.slides._sldIdLst.remove(sldId)

    slides_by_type = {s.type.value: s for s in deck.slides}
    title_slide = slides_by_type.get("title") or (deck.slides[0] if deck.slides else None)
    title_text = title_slide.title.text if title_slide else "Presentation Title"
    subtitle_text = (
        title_slide.subtitle.text if (title_slide and title_slide.subtitle) else ""
    )

    num_template_slides = len(prs.slides)
    content_map: dict[int, dict] = {}

    if num_template_slides <= 6:
        # Structured rubric matching (e.g. SIH hackathon / 6-slide executive templates)
        sol_bullets = (
            [b.text for b in slides_by_type["solution"].bullets]
            if "solution" in slides_by_type
            else []
        )
        prob_bullets = (
            [b.text for b in slides_by_type["problem"].bullets]
            if "problem" in slides_by_type
            else []
        )
        tech_bullets = (
            [b.text for b in slides_by_type["tech_stack"].bullets]
            if "tech_stack" in slides_by_type
            else []
        )
        arch_bullets = (
            [b.text for b in slides_by_type["architecture"].bullets]
            if "architecture" in slides_by_type
            else []
        )
        demo_bullets = (
            [b.text for b in slides_by_type["demo"].bullets]
            if "demo" in slides_by_type
            else []
        )
        feat_bullets = (
            [b.text for b in slides_by_type["features"].bullets]
            if "features" in slides_by_type
            else []
        )
        impact_bullets = (
            [b.text for b in slides_by_type["impact"].bullets]
            if "impact" in slides_by_type
            else []
        )
        road_bullets = (
            [b.text for b in slides_by_type["roadmap"].bullets]
            if "roadmap" in slides_by_type
            else []
        )
        closing_slide = slides_by_type.get("closing")

        content_map = {
            0: {
                "type": "title",
                "title": title_text,
                "subtitle": subtitle_text,
                "header": "",
                "bullets": [],
                "notes": title_slide.notes if title_slide else "",
            },
            1: {
                "type": "solution",
                "title": f"{title_text}: Solution & Innovation",
                "header": "Key Challenges Addressed & Proposed Solution:",
                "bullets": prob_bullets[:2] + sol_bullets[:3],
                "notes": (slides_by_type.get("solution") or title_slide).notes
                if title_slide
                else "",
            },
            2: {
                "type": "tech",
                "title": "TECHNICAL APPROACH & ARCHITECTURE",
                "header": "Core Technologies, Architecture & Workflow:",
                "bullets": tech_bullets[:3] + arch_bullets[:2] + demo_bullets[:2],
                "has_diagram": True,
                "notes": (slides_by_type.get("architecture") or title_slide).notes
                if title_slide
                else "",
            },
            3: {
                "type": "features",
                "title": "FEASIBILITY, VIABILITY & CAPABILITIES",
                "header": "Core Capabilities & Practical Viability:",
                "bullets": feat_bullets if feat_bullets else sol_bullets[:4],
                "notes": (slides_by_type.get("features") or title_slide).notes
                if title_slide
                else "",
            },
            4: {
                "type": "impact",
                "title": "IMPACT, BENEFITS & OUTCOMES",
                "header": "Value Proposition & Measurable Outcomes:",
                "bullets": impact_bullets if impact_bullets else feat_bullets[:3],
                "notes": (slides_by_type.get("impact") or title_slide).notes
                if title_slide
                else "",
            },
            5: {
                "type": "roadmap",
                "title": "RESEARCH, REFERENCES & ROADMAP",
                "header": "Future Milestones & Reference Documentation:",
                "bullets": road_bullets
                if road_bullets
                else (
                    [closing_slide.notes]
                    if closing_slide and closing_slide.notes
                    else []
                ),
                "notes": (slides_by_type.get("roadmap") or title_slide).notes
                if title_slide
                else "",
            },
        }
    else:
        # 1-to-1 matching for 7+ slide decks
        for idx in range(num_template_slides):
            d_slide = deck.slides[idx] if idx < len(deck.slides) else deck.slides[-1]
            content_map[idx] = {
                "type": d_slide.type.value,
                "title": d_slide.title.text,
                "subtitle": d_slide.subtitle.text if d_slide.subtitle else "",
                "header": "",
                "bullets": [b.text for b in d_slide.bullets],
                "has_diagram": d_slide.image_ref == "diagram",
                "notes": d_slide.notes,
            }

    for s_idx, pptx_slide in enumerate(prs.slides):
        info = content_map.get(s_idx, {})
        s_title = info.get("title", "")
        s_subtitle = info.get("subtitle", "")
        s_header = info.get("header", "")
        s_bullets = info.get("bullets", [])
        has_diagram = info.get("has_diagram", False)
        s_notes = info.get("notes", "")

        title_shape = None
        subtitle_shape = None
        body_shape = None
        picture_shape = None
        details_shape = None

        text_candidates = []
        for shape in pptx_slide.shapes:
            if shape.is_placeholder:
                ptype = shape.placeholder_format.type
                if ptype in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                    title_shape = shape
                elif ptype == PP_PLACEHOLDER.SUBTITLE:
                    subtitle_shape = shape
                elif ptype in (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT):
                    body_shape = shape
                elif ptype == PP_PLACEHOLDER.PICTURE:
                    picture_shape = shape
                elif ptype in (
                    PP_PLACEHOLDER.DATE,
                    PP_PLACEHOLDER.FOOTER,
                    PP_PLACEHOLDER.SLIDE_NUMBER,
                ):
                    continue
            else:
                if shape.has_text_frame:
                    txt = shape.text_frame.text
                    txt_lower = txt.lower()
                    if "problem statement" in txt_lower or "team id" in txt_lower:
                        details_shape = shape
                    elif "your team name" in txt_lower or len(txt.strip()) == 0:
                        continue
                    else:
                        area = shape.width * shape.height
                        text_candidates.append((area, shape))
                elif shape.shape_type == 13:  # Picture shape
                    is_corner_or_header_logo = (
                        shape.top < Inches(1.2)
                        or (shape.width < Inches(3.0) and shape.height < Inches(1.8))
                    )
                    if not is_corner_or_header_logo and picture_shape is None:
                        picture_shape = shape

        if body_shape is None and text_candidates:
            text_candidates.sort(key=lambda x: x[0], reverse=True)
            body_shape = text_candidates[0][1]

        # Check if there is an explicit diagram target shape by name or text
        diagram_target_shape = None
        for shape in pptx_slide.shapes:
            if shape in (title_shape, subtitle_shape, details_shape, body_shape):
                continue
            s_name = (shape.name or "").lower()
            s_txt = shape.text_frame.text.lower() if shape.has_text_frame else ""
            if any(
                k in s_name or k in s_txt
                for k in [
                    "diagram",
                    "architecture",
                    "flow chart",
                    "flowchart",
                    "methodology",
                    "prototype",
                    "process diagram",
                ]
            ):
                if shape.top > Inches(1.2) and shape.width > Inches(2.5):
                    diagram_target_shape = shape
                    break

        # 1. Populate Title
        if title_shape and title_shape.has_text_frame and s_title:
            t_style = _extract_font_style(title_shape, Pt(24))
            tf = title_shape.text_frame
            orig_title = tf.text.strip()
            should_update = False
            if s_idx == 0:
                should_update = True
            elif "IDEA TITLE" in orig_title or orig_title.lower().startswith("title"):
                should_update = True
            elif not orig_title:
                should_update = True

            if should_update:
                tf.text = s_title
                if tf.paragraphs:
                    _apply_font_style(tf.paragraphs[0], t_style)

        # 2. Populate Subtitle
        if subtitle_shape and subtitle_shape.has_text_frame and s_subtitle:
            sub_style = _extract_font_style(subtitle_shape, Pt(14))
            subtitle_shape.text_frame.text = s_subtitle
            if subtitle_shape.text_frame.paragraphs:
                _apply_font_style(subtitle_shape.text_frame.paragraphs[0], sub_style)

        # 3. Populate Details on title slide
        if details_shape and s_idx == 0 and details_shape.has_text_frame:
            title_meta = {}
            if title_slide:
                sources = []
                if title_slide.subtitle:
                    sources.append(title_slide.subtitle.text)
                if title_slide.notes:
                    sources.append(title_slide.notes)
                for b in title_slide.bullets:
                    sources.append(b.text)

                for src in sources:
                    for part in src.split("\n"):
                        p_clean = part.strip()
                        p_lower = p_clean.lower()
                        if any(
                            k in p_lower
                            for k in [
                                "problem statement id",
                                "ps id",
                                "statement id",
                                "psid",
                            ]
                        ):
                            val = (
                                p_clean.split(":")[-1]
                                if ":" in p_clean
                                else p_clean.split("-")[-1]
                            )
                            val = val.strip()
                            if val:
                                title_meta["ps_id"] = val
                        elif any(k in p_lower for k in ["team id", "teamid"]):
                            val = (
                                p_clean.split(":")[-1]
                                if ":" in p_clean
                                else p_clean.split("-")[-1]
                            )
                            val = val.strip()
                            if val:
                                title_meta["team_id"] = val
                        elif "team name" in p_lower:
                            val = (
                                p_clean.split(":")[-1]
                                if ":" in p_clean
                                else p_clean.split("-")[-1]
                            )
                            val = val.strip()
                            if val:
                                title_meta["team_name"] = val
                        elif "theme" in p_lower:
                            val = (
                                p_clean.split(":")[-1]
                                if ":" in p_clean
                                else p_clean.split("-")[-1]
                            )
                            val = val.strip()
                            if val:
                                title_meta["theme"] = val
                        elif "category" in p_lower:
                            val = (
                                p_clean.split(":")[-1]
                                if ":" in p_clean
                                else p_clean.split("-")[-1]
                            )
                            val = val.strip()
                            if val:
                                title_meta["category"] = val
                        elif p_clean.isdigit() and len(p_clean) in (3, 4, 5, 6):
                            if "ps_id" not in title_meta:
                                title_meta["ps_id"] = p_clean

            lines = details_shape.text_frame.text.split("\n")
            new_lines = []
            for line in lines:
                l_lower = line.lower()
                sep = (
                    "–"
                    if "–" in line
                    else "-"
                    if "-" in line
                    else ":"
                    if ":" in line
                    else ""
                )
                prefix = line.split(sep)[0].strip() if sep else line.strip()

                if "problem statement title" in l_lower:
                    new_lines.append(f"{prefix} – {title_text}: {subtitle_text}")
                elif any(
                    k in l_lower
                    for k in ["problem statement id", "ps id", "statement id"]
                ):
                    ps_val = title_meta.get("ps_id", "2303")
                    new_lines.append(f"{prefix} – {ps_val}")
                elif "team name" in l_lower and "(" in line:
                    t_name = title_meta.get("team_name", "DeckAgent Team")
                    new_lines.append(f"{prefix} – {t_name}")
                elif "team id" in l_lower:
                    t_id = title_meta.get("team_id")
                    if t_id:
                        new_lines.append(f"{prefix} – {t_id}")
                    else:
                        new_lines.append(line)
                elif "theme" in l_lower and title_meta.get("theme"):
                    new_lines.append(f"{prefix} – {title_meta['theme']}")
                elif "category" in l_lower and title_meta.get("category"):
                    new_lines.append(f"{prefix} – {title_meta['category']}")
                else:
                    new_lines.append(line)
            details_shape.text_frame.text = "\n".join(new_lines)

        # 4. Populate Body
        if body_shape and body_shape.has_text_frame and s_bullets:
            b_style = _extract_font_style(body_shape, Pt(13))
            tf = body_shape.text_frame
            tf.clear()

            if s_header:
                p0 = tf.paragraphs[0]
                p0.text = s_header
                h_style = dict(b_style)
                h_style["bold"] = True
                if h_style.get("size"):
                    h_style["size"] = int(h_style["size"] * 1.08)
                _apply_font_style(p0, h_style)

            for b in s_bullets:
                p = tf.add_paragraph()
                p.text = f"• {b}"
                _apply_font_style(p, b_style)

        # 5. Insert Diagram if requested
        if has_diagram and diagram_png is not None:
            diag_file = Path(diagram_png)
            if diag_file.exists() and diag_file.is_file():
                img_w, img_h = _get_png_dimensions(diag_file)
                if img_w > 0 and img_h > 0:
                    target_rect = None
                    shape_to_remove = None

                    if diagram_target_shape:
                        target_rect = (
                            diagram_target_shape.left,
                            diagram_target_shape.top,
                            diagram_target_shape.width,
                            diagram_target_shape.height,
                        )
                        shape_to_remove = diagram_target_shape
                    elif picture_shape:
                        target_rect = (
                            picture_shape.left,
                            picture_shape.top,
                            picture_shape.width,
                            picture_shape.height,
                        )
                        shape_to_remove = picture_shape
                    elif body_shape:
                        orig_w = body_shape.width
                        orig_l = body_shape.left
                        orig_t = body_shape.top

                        # Split body shape to left side (48% width)
                        new_body_w = int(orig_w * 0.48)
                        body_shape.width = new_body_w

                        # Find footer top to prevent covering footer
                        footers = [
                            s.top for s in pptx_slide.shapes if s.top > Inches(4.5)
                        ]
                        footer_top = min(footers) if footers else prs.slide_height

                        rem_left = orig_l + new_body_w + Inches(0.3)
                        rem_width = int(prs.slide_width - rem_left - Inches(0.6))
                        rem_top = Inches(1.8) if orig_t > Inches(1.5) else orig_t
                        rem_height = max(
                            Inches(2.5), footer_top - rem_top - Inches(0.4)
                        )

                        if rem_width > Inches(2.0):
                            target_rect = (rem_left, rem_top, rem_width, rem_height)

                    if target_rect:
                        b_left, b_top, b_w, b_h = target_rect
                        scale = min(b_w / img_w, b_h / img_h)
                        pic_w = int(img_w * scale)
                        pic_h = int(img_h * scale)
                        pic_l = b_left + int((b_w - pic_w) / 2)
                        pic_t = b_top + int((b_h - pic_h) / 2)

                        if shape_to_remove:
                            sp = shape_to_remove._element
                            parent = sp.getparent()
                            if parent is not None:
                                parent.remove(sp)

                        pptx_slide.shapes.add_picture(
                            str(diag_file), pic_l, pic_t, pic_w, pic_h
                        )

        # 6. Notes
        if s_notes:
            pptx_slide.notes_slide.notes_text_frame.text = s_notes


def render_deck(
    deck: Deck,
    manifest: TemplateManifest,
    template_path: Path,
    out_path: Path,
    diagram_png: Path | None = None,
) -> None:
    template_file = Path(template_path)
    if not template_file.exists():
        from app.config import get_settings

        fallback = get_settings().templates_dir / "default.pptx"
        if fallback.exists():
            template_file = fallback
        else:
            candidates = sorted(get_settings().templates_dir.glob("*.pptx"))
            if candidates:
                template_file = candidates[0]

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation(str(template_file))

    # If the template file already contains designed slides (e.g. user-provided presentation deck),
    # populate its exact slides and placeholders directly.
    if len(prs.slides) > 0:
        _render_deck_into_existing_slides(prs, deck, manifest, diagram_png)
        prs.save(str(out_file))
        return

    # Delete every existing slide properly to prevent PowerPoint repair prompts (for empty master templates)
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
