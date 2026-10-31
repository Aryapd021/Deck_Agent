import colorsys
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Literal
import xml.etree.ElementTree as ET

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

from app.core.errors import ApiError, ErrorCode
from app.core.storage import template_manifest_path
from app.models.api import (
    FitLimits,
    LayoutInfo,
    PlaceholderInfo,
    SlideAnalysisReport,
    SlotMap,
    TemplateManifest,
    TemplateProfile,
    TemplateSlot,
)

SLIDE_TYPES: list[str] = [
    "title",
    "problem",
    "solution",
    "features",
    "architecture",
    "tech_stack",
    "demo",
    "impact",
    "roadmap",
    "closing",
]

NAME_PRIORITIES: dict[str, list[str]] = {
    "title": ["title slide"],
    "closing": ["closing", "thank", "title slide", "section header"],
    "architecture": ["picture with caption", "content with caption", "picture"],
    "demo": ["picture with caption", "content with caption", "picture"],
    "problem": ["title and content", "content", "body"],
    "solution": ["title and content", "content", "body"],
    "features": ["title and content", "content", "body"],
    "tech_stack": ["title and content", "content", "body"],
    "impact": ["title and content", "content", "body"],
    "roadmap": ["title and content", "content", "body"],
}


def _compute_placeholder_char_limit(ptype: int, width: int, height: int) -> int:
    width_pt = width / 12700.0 if width > 0 else 0
    height_pt = height / 12700.0 if height > 0 else 0

    if ptype in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
        chars_per_line = width_pt / (0.5 * 32.0) if width_pt > 0 else 70.0
        lines_that_fit = height_pt / (1.2 * 32.0) if height_pt > 0 else 2.0
        effective_lines = min(2.0, max(1.0, lines_that_fit))
        raw_max = math.floor(chars_per_line * effective_lines)
        return int(min(70, max(30, raw_max)))

    elif ptype == PP_PLACEHOLDER.SUBTITLE:
        chars_per_line = width_pt / (0.5 * 24.0) if width_pt > 0 else 60.0
        lines_that_fit = height_pt / (1.2 * 24.0) if height_pt > 0 else 2.0
        effective_lines = min(3.0, max(1.0, lines_that_fit))
        raw_max = math.floor(chars_per_line * effective_lines)
        return int(max(40, raw_max))

    elif ptype in (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT):
        chars_per_line = width_pt / (0.5 * 20.0) if width_pt > 0 else 50.0
        lines_that_fit = height_pt / (1.2 * 20.0) if height_pt > 0 else 5.0
        effective_lines = max(1.0, lines_that_fit)
        raw_max = math.floor(chars_per_line * effective_lines)
        return int(max(50, raw_max))

    return 0


def _choose_layout_index(slide_type: str, layouts: list[LayoutInfo]) -> int:
    priorities = NAME_PRIORITIES.get(slide_type, ["title and content", "content", "body"])
    for substr in priorities:
        for layout in layouts:
            if substr in layout.name.lower():
                return layout.index

    # Score by placeholder types
    def score_layout(layout: LayoutInfo) -> int:
        ph_types = {ph.type for ph in layout.placeholders}
        has_title = PP_PLACEHOLDER.TITLE in ph_types or PP_PLACEHOLDER.CENTER_TITLE in ph_types
        has_subtitle = PP_PLACEHOLDER.SUBTITLE in ph_types
        has_picture = PP_PLACEHOLDER.PICTURE in ph_types
        has_body = PP_PLACEHOLDER.BODY in ph_types or PP_PLACEHOLDER.OBJECT in ph_types

        score = 0
        if slide_type == "title":
            if has_title and has_subtitle:
                score += 10
            elif has_title:
                score += 5
        elif slide_type in ("architecture", "demo"):
            if has_title and has_picture:
                score += 10
            elif has_picture:
                score += 8
            elif has_title and has_body:
                score += 4
        elif slide_type == "closing":
            if has_title and has_subtitle:
                score += 10
            elif has_title and has_body:
                score += 8
            elif has_title:
                score += 5
        else:  # content types
            if has_title and has_body:
                score += 10
            elif has_title:
                score += 4
        return score

    best_index = -1
    best_score = 0
    for layout in layouts:
        s = score_layout(layout)
        if s > best_score:
            best_score = s
            best_index = layout.index

    if best_score > 0 and best_index >= 0:
        return best_index

    # Final fallback: layout named "Title and Content", else first layout with title + body
    for layout in layouts:
        if "title and content" in layout.name.lower():
            return layout.index

    for layout in layouts:
        ph_types = {ph.type for ph in layout.placeholders}
        has_title = PP_PLACEHOLDER.TITLE in ph_types or PP_PLACEHOLDER.CENTER_TITLE in ph_types
        has_body = PP_PLACEHOLDER.BODY in ph_types or PP_PLACEHOLDER.OBJECT in ph_types
        if has_title and has_body:
            return layout.index

    for layout in layouts:
        ph_types = {ph.type for ph in layout.placeholders}
        if PP_PLACEHOLDER.TITLE in ph_types or PP_PLACEHOLDER.CENTER_TITLE in ph_types:
            return layout.index

    return 0


def _find_fallback_layout(layouts: list[LayoutInfo]) -> LayoutInfo:
    for layout in layouts:
        if "title and content" in layout.name.lower():
            return layout
    for layout in layouts:
        ph_types = {ph.type for ph in layout.placeholders}
        if (PP_PLACEHOLDER.TITLE in ph_types or PP_PLACEHOLDER.CENTER_TITLE in ph_types) and (
            PP_PLACEHOLDER.BODY in ph_types or PP_PLACEHOLDER.OBJECT in ph_types
        ):
            return layout
    for layout in layouts:
        ph_types = {ph.type for ph in layout.placeholders}
        if PP_PLACEHOLDER.TITLE in ph_types or PP_PLACEHOLDER.CENTER_TITLE in ph_types:
            return layout
    return layouts[0]


def _compute_layout_slots(layout: LayoutInfo) -> SlotMap:
    title_idx: int | None = None
    subtitle_idx: int | None = None
    picture_idx: int | None = None

    for ph in layout.placeholders:
        if ph.type in (PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER):
            continue
        if title_idx is None and ph.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
            title_idx = ph.idx
        elif subtitle_idx is None and ph.type == PP_PLACEHOLDER.SUBTITLE:
            subtitle_idx = ph.idx
        elif picture_idx is None and ph.type == PP_PLACEHOLDER.PICTURE:
            picture_idx = ph.idx

    body_idx: int | None = None
    for ph in layout.placeholders:
        if ph.type in (PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER):
            continue
        if ph.type in (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT):
            if ph.idx not in (title_idx, subtitle_idx, picture_idx):
                body_idx = ph.idx
                break

    return SlotMap(
        title_idx=title_idx,
        subtitle_idx=subtitle_idx,
        body_idx=body_idx,
        picture_idx=picture_idx,
    )


def _compute_fit_limits(layout: LayoutInfo, slot: SlotMap) -> FitLimits:
    ph_by_idx = {ph.idx: ph for ph in layout.placeholders}

    # 1. Title limit
    title_max = 70
    if slot.title_idx is not None and slot.title_idx in ph_by_idx:
        title_ph = ph_by_idx[slot.title_idx]
        if title_ph.width > 0 and title_ph.height > 0:
            width_pt = title_ph.width / 12700.0
            height_pt = title_ph.height / 12700.0
            chars_per_line = width_pt / (0.5 * 32.0)
            lines_that_fit = height_pt / (1.2 * 32.0)
            raw_title_max = math.floor(chars_per_line * min(2.0, max(1.0, lines_that_fit)))
            title_max = int(min(70, max(30, raw_title_max)))

    # 2. Body limit
    if slot.body_idx is None:
        bullets_max = 0
        bullet_max = 100
    else:
        bullet_max = 100
        bullets_max = 5
        if slot.body_idx in ph_by_idx:
            body_ph = ph_by_idx[slot.body_idx]
            if body_ph.width > 0 and body_ph.height > 0:
                width_pt = body_ph.width / 12700.0
                height_pt = body_ph.height / 12700.0
                chars_per_line = width_pt / (0.5 * 20.0)
                raw_bullet_max = math.floor(chars_per_line * 2.0)
                bullet_max = int(min(100, max(50, raw_bullet_max)))
                raw_bullets_max = math.floor(height_pt / (1.2 * 20.0 * 2.0))
                bullets_max = int(min(5, max(3, raw_bullets_max)))

    return FitLimits(
        title_max=title_max,
        bullet_max=bullet_max,
        bullets_max=bullets_max,
        notes_max=600,
    )


def _compute_slots_and_limits(
    layouts: list[LayoutInfo], layout_map: dict[str, int]
) -> tuple[dict[str, SlotMap], dict[str, FitLimits], list[str], dict[str, int]]:
    layout_by_idx = {l.index: l for l in layouts}
    slots: dict[str, SlotMap] = {}
    limits: dict[str, FitLimits] = {}
    warnings: list[str] = []
    final_layout_map = dict(layout_map)

    fallback_layout = _find_fallback_layout(layouts)

    for st in SLIDE_TYPES:
        chosen_idx = final_layout_map.get(st)
        if chosen_idx is None or chosen_idx not in layout_by_idx:
            chosen_idx = fallback_layout.index
            final_layout_map[st] = chosen_idx
            warnings.append(
                f"Slide type '{st}' mapped to nonexistent layout index {layout_map.get(st)}; "
                f"fell back to layout {fallback_layout.index} ('{fallback_layout.name}')."
            )

        layout = layout_by_idx[chosen_idx]
        slot = _compute_layout_slots(layout)

        # Validation rule 1: Every slide type needs title_idx
        if slot.title_idx is None:
            orig_idx = chosen_idx
            final_layout_map[st] = fallback_layout.index
            layout = fallback_layout
            slot = _compute_layout_slots(layout)
            warnings.append(
                f"Slide type '{st}' has no title placeholder in layout {orig_idx}; "
                f"fell back to layout {fallback_layout.index} ('{fallback_layout.name}')."
            )

        # Validation rule 2: architecture and demo need picture_idx or body_idx
        if st in ("architecture", "demo") and slot.picture_idx is None and slot.body_idx is None:
            orig_idx = chosen_idx
            final_layout_map[st] = fallback_layout.index
            layout = fallback_layout
            slot = _compute_layout_slots(layout)
            warnings.append(
                f"Slide type '{st}' requires a picture or content area in layout {orig_idx}; "
                f"fell back to layout {fallback_layout.index} ('{fallback_layout.name}')."
            )

        slots[st] = slot
        limits[st] = _compute_fit_limits(layout, slot)

    return slots, limits, warnings, final_layout_map


def extract_palette(pptx_path: Path | str) -> dict[str, str]:
    """Read the theme part of the first master and parse a:clrScheme;
    return keys background (lt1), text (dk1), primary (accent1), secondary (accent2),
    accent (accent3), plus dark2/light2 if present, as "#RRGGBB" strings (srgbClr val, or sysClr lastClr).
    On any parsing problem return {} so callers use the default.
    """
    try:
        prs = Presentation(str(pptx_path))
        if not prs.slide_masters:
            return {}
        master = prs.slide_masters[0]
        theme_part = None
        for rel in master.part.rels.values():
            if "theme" in rel.reltype:
                theme_part = rel.target_part
                break
        if theme_part is None:
            return {}

        root = ET.fromstring(theme_part.blob)
        clr_scheme = None
        for elem in root.iter():
            if elem.tag.endswith("clrScheme"):
                clr_scheme = elem
                break
        if clr_scheme is None:
            return {}

        scheme_map = {
            "lt1": "background",
            "dk1": "text",
            "accent1": "primary",
            "accent2": "secondary",
            "accent3": "accent",
            "dk2": "dark2",
            "lt2": "light2",
        }

        palette: dict[str, str] = {}
        for child in clr_scheme:
            tag_name = child.tag.split("}")[-1]
            if tag_name in scheme_map:
                key = scheme_map[tag_name]
                hex_val = None
                for sub in child:
                    sub_tag = sub.tag.split("}")[-1]
                    if sub_tag == "srgbClr":
                        hex_val = sub.get("val")
                        break
                    elif sub_tag == "sysClr":
                        hex_val = sub.get("lastClr")
                        break
                if hex_val:
                    hex_val = hex_val.strip().lstrip("#")
                    if len(hex_val) == 6:
                        palette[key] = f"#{hex_val.upper()}"
        return palette
    except Exception:
        return {}


def relative_luminance(hex_str: str) -> float:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) != 6:
        return 0.5
    r, g, b = [int(hex_str[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def srgb_to_lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * srgb_to_lin(r) + 0.7152 * srgb_to_lin(g) + 0.0722 * srgb_to_lin(b)


def contrast_ratio(hex1: str, hex2: str) -> float:
    l1 = relative_luminance(hex1)
    l2 = relative_luminance(hex2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def ensure_contrast(text_hex: str, bg_hex: str, min_ratio: float = 4.5) -> str:
    """Returns text_hex if contrast >= min_ratio, else pure black or pure white whichever has higher contrast."""
    if contrast_ratio(text_hex, bg_hex) >= min_ratio:
        return text_hex
    c_white = contrast_ratio("#FFFFFF", bg_hex)
    c_black = contrast_ratio("#0F172A", bg_hex)
    return "#FFFFFF" if c_white >= c_black else "#0F172A"


def hex_to_hsl(hex_code: str) -> tuple[float, float, float]:
    hex_code = hex_code.lstrip("#")
    r, g, b = [int(hex_code[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360.0, s, l


def hsl_to_hex(h: float, s: float, l: float) -> str:
    h_norm = (h % 360.0) / 360.0
    r, g, b = colorsys.hls_to_rgb(h_norm, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return f"#{int(round(r * 255)):02X}{int(round(g * 255)):02X}{int(round(b * 255)):02X}"


def hue_distance(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % 360.0
    return 360.0 - d if d > 180.0 else d


STOCK_OFFICE_ACCENTS: set[str] = {
    "4F81BD", "C0504D", "9BBB59", "8064A2", "4BACC6", "F79646",
    "5B9BD5", "ED7D31", "A5A5A5", "FFC000", "4472C4", "70AD47",
}

PRO_PALETTE: dict[str, str] = {
    "primary": "#2563EB",
    "secondary": "#0D9488",
    "tertiary": "#D97706",
    "quaternary": "#7C3AED",
    "neutral": "#64748B",
}


def classify_slide_shapes(
    slide: Any,
    slide_width: int,
    slide_height: int,
    repeated_texts: set[str],
) -> tuple[list[Any], list[Any]]:
    """Deterministically classifies shapes on a slide into (static_shapes, fillable_shapes)."""
    static_shapes: list[Any] = []
    fillable_shapes: list[Any] = []
    slide_area = slide_width * slide_height

    for shape in slide.shapes:
        # Pictures, groups, lines, connectors
        if (
            shape.shape_type
            in (
                MSO_SHAPE_TYPE.PICTURE,
                MSO_SHAPE_TYPE.GROUP,
                MSO_SHAPE_TYPE.LINE,
            )
            or hasattr(shape, "image")
        ):
            static_shapes.append(shape)
            continue

        # Placeholders
        if shape.is_placeholder:
            p_type = shape.placeholder_format.type
            if p_type in (
                PP_PLACEHOLDER.FOOTER,
                PP_PLACEHOLDER.SLIDE_NUMBER,
                PP_PLACEHOLDER.DATE,
            ):
                static_shapes.append(shape)
                continue
            elif p_type in (
                PP_PLACEHOLDER.BODY,
                PP_PLACEHOLDER.OBJECT,
                PP_PLACEHOLDER.TABLE,
                PP_PLACEHOLDER.CHART,
                PP_PLACEHOLDER.PICTURE,
            ):
                fillable_shapes.append(shape)
                continue
            elif p_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                t = shape.text_frame.text.strip() if shape.has_text_frame else ""
                t_lower = t.lower()
                if not t or t_lower in ("click to add title", "title", "presentation title"):
                    fillable_shapes.append(shape)
                else:
                    static_shapes.append(shape)
                continue

        # Non-placeholders with text
        if shape.has_text_frame:
            t = shape.text_frame.text.strip()
            t_lower = t.lower()

            # Text repeated on 2+ slides -> STATIC
            if t_lower and t_lower in repeated_texts:
                static_shapes.append(shape)
                continue

            # Short labels (<= 40 chars) -> STATIC
            if len(t) <= 40:
                static_shapes.append(shape)
                continue

            # All-caps headers -> STATIC
            if t.isupper() and len(t) <= 60:
                static_shapes.append(shape)
                continue

            # Fillable criteria: > 12% slide area, > 120 chars, or bullet chars
            area = shape.width * shape.height
            has_bullets = any(c in t for c in ("•", "–", "-", "*", "\u2022", "\u25cf", "\u25aa"))
            if (area / slide_area > 0.12) or len(t) > 120 or has_bullets:
                fillable_shapes.append(shape)
                continue

            # Fallback
            static_shapes.append(shape)
        else:
            static_shapes.append(shape)

    return static_shapes, fillable_shapes


def compute_free_box(
    slide: Any,
    slide_width: int,
    slide_height: int,
    static_shapes: list[Any],
    fillable_shapes: list[Any],
) -> tuple[int, int, int, int]:
    """Computes the largest rectangle free of static shapes, expanded from the fillable shapes' union."""
    margin_x = int(0.04 * slide_width)
    margin_y = int(0.04 * slide_height)
    slide_area = slide_width * slide_height

    # Exclude full-slide background rects or tall background decorations
    # Any shape covering > 35% of slide area or taller than 50% of slide height is background/column art, NOT a header/footer bar
    non_bg_static = [
        s
        for s in static_shapes
        if not (
            (s.width * s.height > 0.35 * slide_area)
            or (s.height > 0.50 * slide_height)
        )
    ]

    # Top boundary from upper static shapes (titles, headers <= 30% slide height)
    top_shapes = [
        s.top + s.height
        for s in non_bg_static
        if s.top < 0.25 * slide_height and s.height <= 0.30 * slide_height
    ]
    effective_top = max(top_shapes) if top_shapes else int(0.16 * slide_height)

    # Bottom boundary from lower static shapes (footers, page numbers <= 25% slide height)
    bottom_shapes = [
        s.top
        for s in non_bg_static
        if s.top > 0.65 * slide_height and s.height <= 0.25 * slide_height
    ]
    effective_bottom = min(bottom_shapes) if bottom_shapes else (slide_height - margin_y)

    if fillable_shapes:
        min_l = min(s.left for s in fillable_shapes)
        max_r = max(s.left + s.width for s in fillable_shapes)
        min_t = min(s.top for s in fillable_shapes)
    else:
        min_l = margin_x
        max_r = slide_width - margin_x
        min_t = effective_top

    # Ensure box expands vertically to utilize the generous open canvas down to the footer
    box_t = max(margin_y, max(effective_top + int(0.015 * slide_height), min(min_t, effective_top + int(0.02 * slide_height))))
    box_b = min(slide_height - margin_y, effective_bottom - int(0.015 * slide_height))
    box_h = max(int(0.35 * slide_height), box_b - box_t)

    # Ensure box expands horizontally between margins and obstacles
    box_l = max(margin_x, min(min_l, int(0.08 * slide_width)))
    box_r = min(slide_width - margin_x, max(max_r, int(0.92 * slide_width)))
    box_w = max(int(0.30 * slide_width), box_r - box_l)

    return (int(box_l), int(box_t), int(box_w), int(box_h))


def extract_slot_info(
    slide: Any, slide_index: int, fillable_shapes: list[Any]
) -> tuple[str, str]:
    """Extracts slot_title and hint from a template slide."""
    slot_title = ""
    for shape in slide.shapes:
        if shape.is_placeholder and shape.placeholder_format.type in (
            PP_PLACEHOLDER.TITLE,
            PP_PLACEHOLDER.CENTER_TITLE,
        ):
            if shape.has_text_frame and shape.text_frame.text.strip():
                t = shape.text_frame.text.strip()
                if t.lower() not in ("click to add title", "title", "presentation title"):
                    slot_title = t
                    break

    if not slot_title:
        text_shapes = [
            s for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip()
        ]
        if text_shapes:
            text_shapes.sort(key=lambda s: (s.top, -s.width))
            slot_title = text_shapes[0].text_frame.text.strip().split("\n")[0][:60]

    if not slot_title:
        slot_title = f"Slide {slide_index + 1}"

    slot_title = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", slot_title).strip()

    hint = ""
    for f in fillable_shapes:
        if f.has_text_frame and f.text_frame.text.strip():
            hint = f.text_frame.text.strip().replace("\n", " ")[:150]
            break

    return slot_title, hint


_PROFILE_CACHE: dict[str, TemplateProfile] = {}


def _do_analyze_template(pptx_path: Path | str) -> TemplateProfile:
    path = Path(pptx_path)
    prs = Presentation(str(path))
    slide_w = int(prs.slide_width)
    slide_h = int(prs.slide_height)
    scale = slide_h / 6858000.0

    # 1. Read Theme Part
    theme_name = ""
    dk1 = "#1E293B"
    lt1 = "#FFFFFF"
    dk2 = "#0F172A"
    lt2 = "#F8FAFC"
    accent1 = "#3B82F6"
    accent2 = "#10B981"
    accent3 = "#F59E0B"
    accent4 = "#EF4444"
    accent5 = "#8B5CF6"
    accent6 = "#06B6D4"
    major_font = "Calibri"
    minor_font = "Calibri"

    try:
        if prs.slide_masters:
            master = prs.slide_masters[0]
            theme_part = None
            for rel in master.part.rels.values():
                if "theme" in rel.reltype:
                    theme_part = rel.target_part
                    break
            if theme_part is not None:
                root = ET.fromstring(theme_part.blob)
                clr_scheme = None
                for elem in root.iter():
                    if elem.tag.endswith("clrScheme"):
                        clr_scheme = elem
                        theme_name = clr_scheme.get("name", "")
                    elif elem.tag.endswith("majorFont"):
                        for sub in elem:
                            if sub.tag.endswith("latin") and sub.get("typeface"):
                                major_font = sub.get("typeface")
                    elif elem.tag.endswith("minorFont"):
                        for sub in elem:
                            if sub.tag.endswith("latin") and sub.get("typeface"):
                                minor_font = sub.get("typeface")

                if clr_scheme is not None:
                    scheme_tags = {
                        "dk1": "dk1",
                        "lt1": "lt1",
                        "dk2": "dk2",
                        "lt2": "lt2",
                        "accent1": "accent1",
                        "accent2": "accent2",
                        "accent3": "accent3",
                        "accent4": "accent4",
                        "accent5": "accent5",
                        "accent6": "accent6",
                    }
                    extracted_colors: dict[str, str] = {}
                    for child in clr_scheme:
                        tag_name = child.tag.split("}")[-1]
                        if tag_name in scheme_tags:
                            h_val = None
                            for sub in child:
                                sub_tag = sub.tag.split("}")[-1]
                                if sub_tag == "srgbClr":
                                    h_val = sub.get("val")
                                    break
                                elif sub_tag == "sysClr":
                                    h_val = sub.get("lastClr")
                                    break
                            if h_val:
                                h_val = h_val.strip().lstrip("#")
                                if len(h_val) == 6:
                                    extracted_colors[tag_name] = f"#{h_val.upper()}"

                    dk1 = extracted_colors.get("dk1", dk1)
                    lt1 = extracted_colors.get("lt1", lt1)
                    dk2 = extracted_colors.get("dk2", dk2)
                    lt2 = extracted_colors.get("lt2", lt2)
                    accent1 = extracted_colors.get("accent1", accent1)
                    accent2 = extracted_colors.get("accent2", accent2)
                    accent3 = extracted_colors.get("accent3", accent3)
                    accent4 = extracted_colors.get("accent4", accent4)
                    accent5 = extracted_colors.get("accent5", accent5)
                    accent6 = extracted_colors.get("accent6", accent6)
    except Exception:
        pass

    # 2. Background Luminance
    bg_lum: Literal["dark", "light"] = "light"
    try:
        if prs.slide_masters:
            master = prs.slide_masters[0]
            bg = master.background
            if (
                bg
                and bg.fill
                and hasattr(bg.fill, "fore_color")
                and hasattr(bg.fill.fore_color, "rgb")
            ):
                rgb = bg.fill.fore_color.rgb
                hex_c = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
                bg_lum = "dark" if relative_luminance(hex_c) < 0.5 else "light"
            elif relative_luminance(lt1) < 0.5:
                bg_lum = "dark"
    except Exception:
        pass

    # 3. Palette Policy
    raw_accents = [accent1, accent2, accent3, accent4, accent5, accent6]
    is_stock_office = (
        "office" in theme_name.lower()
        or any(a.lstrip("#").upper() in STOCK_OFFICE_ACCENTS for a in raw_accents[:3])
    )

    if is_stock_office:
        primary = PRO_PALETTE["primary"]
        secondary = PRO_PALETTE["secondary"]
        tertiary = PRO_PALETTE["tertiary"]
        quaternary = PRO_PALETTE["quaternary"]
        neutral = PRO_PALETTE["neutral"]
    else:
        # Check distinguishable accents by hue distance
        hues = [hex_to_hsl(a)[0] for a in raw_accents]
        distinct_accents: list[str] = [raw_accents[0]]
        for a, h in zip(raw_accents[1:], hues[1:], strict=False):
            if all(hue_distance(h, hex_to_hsl(da)[0]) >= 25.0 for da in distinct_accents):
                distinct_accents.append(a)

        if len(distinct_accents) < 4:
            # Monochrome palette: synthesize missing accents by rotating accent1 hue (+35, +70, +140, +210, +280)
            h1, s1, l1 = hex_to_hsl(accent1)
            s1 = max(0.4, min(0.9, s1))
            l1 = max(0.35, min(0.65, l1))
            synthesized = [accent1]
            for deg in (35.0, 70.0, 140.0, 210.0, 280.0):
                synthesized.append(hsl_to_hex(h1 + deg, s1, l1))
            distinct_accents = synthesized

        target_bg = lt1 if bg_lum == "light" else dk1

        def adjust_contrast(c_hex: str) -> str:
            h, s, l = hex_to_hsl(c_hex)
            if bg_lum == "light":
                while contrast_ratio(hsl_to_hex(h, s, l), target_bg) < 3.0 and l > 0.15:
                    l -= 0.05
            else:
                while contrast_ratio(hsl_to_hex(h, s, l), target_bg) < 3.0 and l < 0.85:
                    l += 0.05
            return hsl_to_hex(h, s, l)

        adjusted_accents = [adjust_contrast(a) for a in distinct_accents]
        primary = adjusted_accents[0]
        secondary = adjusted_accents[1] if len(adjusted_accents) > 1 else PRO_PALETTE["secondary"]
        tertiary = adjusted_accents[2] if len(adjusted_accents) > 2 else PRO_PALETTE["tertiary"]
        quaternary = adjusted_accents[3] if len(adjusted_accents) > 3 else PRO_PALETTE["quaternary"]
        neutral = dk2 if bg_lum == "light" else lt2

    # 4. Slide Classification & Slots
    repeated_texts: set[str] = set()
    text_counts: dict[str, int] = {}
    for sld in prs.slides:
        for shp in sld.shapes:
            if shp.has_text_frame:
                t = shp.text_frame.text.strip().lower()
                if t:
                    text_counts[t] = text_counts.get(t, 0) + 1
    repeated_texts = {t for t, count in text_counts.items() if count >= 2}

    slots: list[TemplateSlot] = []
    analysis_reports: list[SlideAnalysisReport] = []
    fillable_slide_count = 0

    for idx, sld in enumerate(prs.slides):
        stat_shapes, fill_shapes = classify_slide_shapes(
            sld, slide_w, slide_h, repeated_texts
        )
        if len(fill_shapes) > 0:
            fillable_slide_count += 1

        fbox = compute_free_box(sld, slide_w, slide_h, stat_shapes, fill_shapes)
        slot_title, hint = extract_slot_info(sld, idx, fill_shapes)

        slots.append(TemplateSlot(slot_index=idx, slot_title=slot_title, hint=hint))
        analysis_reports.append(
            SlideAnalysisReport(
                slide_index=idx,
                static_count=len(stat_shapes),
                fillable_count=len(fill_shapes),
                free_box=fbox,
                title=slot_title,
            )
        )

    # 5. Determine Mode
    is_default_stock = (
        path.name.lower() in ("default.pptx", "tmpl_1.pptx", "default_tmpl.pptx")
        or "default" in path.stem.lower()
    )
    if is_default_stock:
        mode: Literal["fill", "layouts"] = "layouts"
    else:
        mode = "fill" if fillable_slide_count >= 3 else "layouts"

    return TemplateProfile(
        slide_width=slide_w,
        slide_height=slide_h,
        scale=scale,
        mode=mode,
        background_luminance=bg_lum,
        theme_name=theme_name,
        dk1=dk1,
        lt1=lt1,
        dk2=dk2,
        lt2=lt2,
        accent1=accent1,
        accent2=accent2,
        accent3=accent3,
        accent4=accent4,
        accent5=accent5,
        accent6=accent6,
        major_font=major_font or "Calibri",
        minor_font=minor_font or "Calibri",
        primary=primary,
        secondary=secondary,
        tertiary=tertiary,
        quaternary=quaternary,
        neutral=neutral,
        corner_radius_ratio=0.025,
        padding_ratio=0.015,
        gap_ratio=0.018,
        base_body_size_pt=12.0 * scale,
        min_body_size_pt=9.0 * scale,
        slots=slots,
        analysis_reports=analysis_reports,
    )


def analyze_template(pptx_path: Path | str) -> TemplateProfile:
    path = Path(pptx_path)
    try:
        cache_key = f"{path.resolve()}:{path.stat().st_mtime}" if path.exists() else str(pptx_path)
    except Exception:
        cache_key = str(pptx_path)

    if cache_key in _PROFILE_CACHE:
        return _PROFILE_CACHE[cache_key]

    profile = _do_analyze_template(path)
    _PROFILE_CACHE[cache_key] = profile
    return profile


def inspect_template(pptx_path: Path, template_id: str, name: str) -> TemplateManifest:
    path = Path(pptx_path)
    if not path.exists():
        raise ApiError(
            ErrorCode.TEMPLATE_INVALID,
            f"Template file not found at path: {pptx_path}",
        )

    try:
        prs = Presentation(str(path))
    except Exception as e:
        raise ApiError(
            ErrorCode.TEMPLATE_INVALID,
            f"Failed to open PowerPoint presentation: {e}",
        ) from e

    has_any_title = False
    for layout in prs.slide_layouts:
        for ph in layout.placeholders:
            ptype = int(ph.placeholder_format.type)
            if ptype in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                has_any_title = True
                break
        if has_any_title:
            break

    if not has_any_title:
        raise ApiError(
            ErrorCode.TEMPLATE_INVALID,
            "Template is invalid: no slide layout contains a TITLE or CENTER_TITLE placeholder.",
        )

    layouts_info: list[LayoutInfo] = []
    for idx, layout in enumerate(prs.slide_layouts):
        placeholders_info: list[PlaceholderInfo] = []
        for ph in layout.placeholders:
            ptype = int(ph.placeholder_format.type)
            left = int(ph.left) if ph.left is not None else 0
            top = int(ph.top) if ph.top is not None else 0
            width = int(ph.width) if ph.width is not None else 0
            height = int(ph.height) if ph.height is not None else 0
            char_limit = _compute_placeholder_char_limit(ptype, width, height)

            placeholders_info.append(
                PlaceholderInfo(
                    idx=int(ph.placeholder_format.idx),
                    type=ptype,
                    name=str(ph.name or ""),
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                    char_limit=char_limit,
                )
            )

        layouts_info.append(
            LayoutInfo(
                index=idx,
                name=str(layout.name or f"Layout {idx}"),
                placeholders=placeholders_info,
            )
        )

    initial_layout_map: dict[str, int] = {}
    for st in SLIDE_TYPES:
        initial_layout_map[st] = _choose_layout_index(st, layouts_info)

    slots, limits, warnings, final_layout_map = _compute_slots_and_limits(
        layouts_info, initial_layout_map
    )

    slide_width = int(prs.slide_width)
    slide_height = int(prs.slide_height)
    palette = extract_palette(path)

    profile = analyze_template(path)
    for rep in profile.analysis_reports:
        warnings.append(
            f"Slide {rep.slide_index + 1}: {rep.static_count} static shapes (preserved), "
            f"{rep.fillable_count} fillable shapes (replaced), "
            f"free box ({rep.free_box[0]}, {rep.free_box[1]}, {rep.free_box[2]}, {rep.free_box[3]})"
        )

    return TemplateManifest(
        template_id=template_id,
        name=name,
        slide_width=slide_width,
        slide_height=slide_height,
        layouts=layouts_info,
        layout_map=final_layout_map,
        slots=slots,
        limits=limits,
        palette=palette if palette else None,
        warnings=warnings,
        mode=profile.mode,
        profile=profile,
    )


def rebuild_manifest(
    manifest: TemplateManifest, layout_map: dict[str, int] | None = None
) -> TemplateManifest:
    target_layout_map = dict(layout_map) if layout_map is not None else dict(manifest.layout_map)
    slots, limits, warnings, final_layout_map = _compute_slots_and_limits(
        manifest.layouts, target_layout_map
    )
    profile = getattr(manifest, "profile", None)
    mode = getattr(manifest, "mode", profile.mode if profile else "layouts")
    return TemplateManifest(
        template_id=manifest.template_id,
        name=manifest.name,
        slide_width=manifest.slide_width,
        slide_height=manifest.slide_height,
        layouts=manifest.layouts,
        layout_map=final_layout_map,
        slots=slots,
        limits=limits,
        palette=manifest.palette,
        warnings=warnings,
        mode=mode,
        profile=profile,
    )


def save_manifest(m: TemplateManifest) -> Path:
    target_path = template_manifest_path(m.template_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(m.model_dump_json(indent=2), encoding="utf-8")
    return target_path


def load_manifest(template_id: str) -> TemplateManifest:
    target_path = template_manifest_path(template_id)
    if not target_path.exists():
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"Template manifest not found for template_id: {template_id}",
        )
    content = target_path.read_text(encoding="utf-8")
    m = TemplateManifest.model_validate_json(content)
    if m.profile is None:
        pptx_path = template_pptx_path(template_id)
        if pptx_path.exists():
            try:
                m.profile = analyze_template(pptx_path)
                m.mode = m.profile.mode
                save_manifest(m)
            except Exception:
                pass
    return m


@dataclass
class PictureTarget:
    kind: Literal["placeholder", "area"]
    idx: int | None
    left: int
    top: int
    width: int
    height: int
    shrink_body: bool = False


def resolve_picture_target(
    manifest: TemplateManifest, slide_type: str
) -> PictureTarget | None:
    """Resolves the picture insertion target for a given slide type on the fly.
    If manifest.slots[type].picture_idx is set -> kind 'placeholder' with that idx and its box;
    else if body_idx is set -> kind 'area' using the body placeholder's box and shrink_body=True;
    else a default area with 8 percent margins of the slide.
    """
    if not manifest:
        return None

    layout_idx = manifest.layout_map.get(slide_type)
    layout_info = next((l for l in manifest.layouts if l.index == layout_idx), None)
    ph_map = {ph.idx: ph for ph in layout_info.placeholders} if layout_info else {}

    slot = manifest.slots.get(slide_type)
    if slot is not None and slot.picture_idx is not None and slot.picture_idx in ph_map:
        ph = ph_map[slot.picture_idx]
        return PictureTarget(
            kind="placeholder",
            idx=slot.picture_idx,
            left=ph.left,
            top=ph.top,
            width=ph.width,
            height=ph.height,
            shrink_body=False,
        )

    if slot is not None and slot.body_idx is not None and slot.body_idx in ph_map:
        ph = ph_map[slot.body_idx]
        return PictureTarget(
            kind="area",
            idx=slot.body_idx,
            left=ph.left,
            top=ph.top,
            width=ph.width,
            height=ph.height,
            shrink_body=True,
        )

    margin_x = int(manifest.slide_width * 0.08)
    margin_y = int(manifest.slide_height * 0.08)
    return PictureTarget(
        kind="area",
        idx=None,
        left=margin_x,
        top=margin_y,
        width=manifest.slide_width - 2 * margin_x,
        height=manifest.slide_height - 2 * margin_y,
        shrink_body=False,
    )


def set_template(deck: Any, new_manifest: TemplateManifest) -> Any:
    """SetTemplate re-renders the SAME Deck with the new template's profile and mode.
    If the new template is in fill mode and the deck has a different slot count,
    reject with 'Template has N slots, deck has M slides' and offer layouts mode.
    Content and slide count are never altered to fit a template.
    Does NOT call enforce_fit or any LLM.
    """
    mode = getattr(new_manifest, "mode", "layouts")
    if mode == "fill" and new_manifest.profile and len(new_manifest.profile.slots) > 0:
        slot_count = len(new_manifest.profile.slots)
        deck_count = len(deck.slides)
        if slot_count != deck_count:
            raise ApiError(
                ErrorCode.TEMPLATE_INVALID,
                f"Template has {slot_count} slots, deck has {deck_count} slides",
            )

    new_deck = deck.model_copy(deep=True)
    new_deck.template_id = new_manifest.template_id
    return new_deck


def set_theme(deck: Any, theme: str, variant: str | None = None) -> Any:
    """SetTheme switches theme/variant without mutating Deck text."""
    new_deck = deck.model_copy(deep=True)
    new_deck.theme = theme
    if variant is not None:
        new_deck.variant = variant
    return new_deck


SetTemplate = set_template
SetTheme = set_theme

