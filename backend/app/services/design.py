"""Template-Adaptive Design Engine for PowerPoint presentations.

Implements responsive layout grids, dynamic typography fitting,
palette roles, visual needs_input styling, and slot recipes.
All dimensions, colors, and fonts are derived from TemplateProfile and Box.
"""

from collections.abc import Callable
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Literal

from PIL import ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml import parse_xml
from pptx.slide import Slide as PptxSlide
from pptx.util import Inches, Pt

from app.models.api import TemplateProfile
from app.models.deck import Deck, Field, Slide, SlideType, Source


# --- GEOMETRY: BOX & GRID ---


@dataclass
class Box:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height > 0 else 1.0


def place_grid(
    n: int,
    box: Box,
    policy: str = "auto",
    gap_x: int = 0,
    gap_y: int = 0,
) -> list[Box]:
    """Generates n sub-boxes positioned in a grid within box.
    Columns are chosen from box aspect ratio and policy.
    """
    if n <= 0:
        return []
    if n == 1:
        return [Box(box.left, box.top, box.width, box.height)]

    ar = box.aspect_ratio
    if policy == "horizontal" or (policy == "auto" and ar >= 1.5 and n <= 5):
        cols = n
        rows = 1
    elif policy == "vertical" or (policy == "auto" and ar < 0.9):
        cols = 1
        rows = n
    else:
        if n == 2:
            cols = 2 if ar >= 1.0 else 1
        elif n == 3:
            cols = 3 if ar >= 1.3 else 1
        elif n == 4:
            cols = 2 if ar < 1.9 else 4
        elif n in (5, 6):
            cols = 3 if ar >= 1.1 else 2
        else:
            cols = 4 if ar >= 1.7 else 3
        rows = math.ceil(n / cols)

    cell_w = int((box.width - gap_x * (cols - 1)) / cols)
    cell_h = int((box.height - gap_y * (rows - 1)) / rows)

    boxes: list[Box] = []
    for i in range(n):
        r = i // cols
        c = i % cols
        bx = box.left + c * (cell_w + gap_x)
        by = box.top + r * (cell_h + gap_y)
        boxes.append(Box(bx, by, cell_w, cell_h))
    return boxes


# --- COLOR HELPERS & CONTRAST ---


def hex_to_rgb(hex_str: str) -> RGBColor:
    clean = hex_str.lstrip("#")
    if len(clean) != 6:
        clean = "000000"
    return RGBColor(int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))


def relative_luminance(hex_str: str) -> float:
    clean = hex_str.lstrip("#")
    if len(clean) != 6:
        return 0.5
    r, g, b = [int(clean[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]

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
    """Guarantees >= min_ratio contrast against bg_hex."""
    if contrast_ratio(text_hex, bg_hex) >= min_ratio:
        return text_hex
    c_white = contrast_ratio("#FFFFFF", bg_hex)
    c_black = contrast_ratio("#0F172A", bg_hex)
    return "#FFFFFF" if c_white >= c_black else "#0F172A"


def blend_with_white(hex_str: str, factor: float) -> str:
    clean = hex_str.lstrip("#")
    if len(clean) != 6:
        return "FFFFFF"
    r = int(clean[0:2], 16)
    g = int(clean[2:4], 16)
    b = int(clean[4:6], 16)
    r_b = int(r + (255 - r) * factor)
    g_b = int(g + (255 - g) * factor)
    b_b = int(b + (255 - b) * factor)
    return f"#{r_b:02X}{g_b:02X}{b_b:02X}"


def blend_with_black(hex_str: str, factor: float) -> str:
    clean = hex_str.lstrip("#")
    if len(clean) != 6:
        return "000000"
    r = int(clean[0:2], 16)
    g = int(clean[2:4], 16)
    b = int(clean[4:6], 16)
    r_b = int(r * (1.0 - factor))
    g_b = int(g * (1.0 - factor))
    b_b = int(b * (1.0 - factor))
    return f"#{r_b:02X}{g_b:02X}{b_b:02X}"


# --- TOKENS ---


@dataclass
class ProfileTokens:
    font_name: str
    title_font_name: str
    scale: float
    corner_radius: int
    padding: int
    gap: int
    base_body_pt: float
    min_body_pt: float
    bg_lum: Literal["dark", "light"]
    bg_color: str
    primary: str
    secondary: str
    tertiary: str
    quaternary: str
    neutral: str
    text_color: str
    muted_text_color: str
    panel_fills: list[str]
    accents: list[str]

    @classmethod
    def from_profile(
        cls, profile: TemplateProfile, box: Box | None = None
    ) -> "ProfileTokens":
        scale = profile.scale if profile.scale > 0 else 1.0
        ref_w = box.width if box else profile.slide_width
        corner_radius = max(int(ref_w * profile.corner_radius_ratio), 50000)
        padding = max(int(ref_w * profile.padding_ratio), 40000)
        gap = max(int(ref_w * profile.gap_ratio), 50000)
        base_body_pt = profile.base_body_size_pt if profile.base_body_size_pt > 0 else 12.0 * scale
        min_body_pt = profile.min_body_size_pt if profile.min_body_size_pt > 0 else 9.0 * scale
        bg_lum = (
            profile.background_luminance
            or getattr(profile, "background", "light")
            or "light"
        )

        accents = [
            profile.primary or profile.role_primary or "#2563EB",
            profile.secondary or profile.role_secondary or "#0D9488",
            profile.tertiary or profile.role_tertiary or "#D97706",
            profile.quaternary or profile.role_quaternary or "#7C3AED",
            profile.neutral or profile.role_neutral or "#64748B",
            profile.accent1 or "#2563EB",
            profile.accent2 or "#0D9488",
        ]

        if bg_lum == "dark":
            bg_color = profile.dk1 if relative_luminance(profile.dk1) < 0.2 else "#0F172A"
            text_color = profile.lt1 if relative_luminance(profile.lt1) > 0.6 else "#F8FAFC"
            muted_text_color = "#94A3B8"
            panel_fills = [
                blend_with_black(accents[0], 0.75),
                blend_with_black(accents[1], 0.75),
                blend_with_black(accents[2], 0.75),
                blend_with_black(accents[3], 0.75),
                "#1E293B",
            ]
        else:
            bg_color = profile.lt1 if relative_luminance(profile.lt1) > 0.8 else "#FFFFFF"
            text_color = profile.dk1 if relative_luminance(profile.dk1) < 0.3 else "#1E293B"
            muted_text_color = "#64748B"
            panel_fills = [
                blend_with_white(accents[0], 0.88),
                blend_with_white(accents[1], 0.88),
                blend_with_white(accents[2], 0.88),
                blend_with_white(accents[3], 0.88),
                "#F8FAFC",
            ]

        return cls(
            font_name=profile.minor_font or "Calibri",
            title_font_name=profile.major_font or profile.minor_font or "Calibri",
            scale=scale,
            corner_radius=corner_radius,
            padding=padding,
            gap=gap,
            base_body_pt=base_body_pt,
            min_body_pt=min_body_pt,
            bg_lum=bg_lum,
            bg_color=bg_color,
            primary=accents[0],
            secondary=accents[1],
            tertiary=accents[2],
            quaternary=accents[3],
            neutral=accents[4],
            text_color=text_color,
            muted_text_color=muted_text_color,
            panel_fills=panel_fills,
            accents=accents,
        )


# --- TEXT MEASUREMENT & FITTING ---


def measure_text_box(
    text: str,
    font_name: str,
    font_size_pt: float,
    max_width_pt: float,
) -> tuple[float, float, int]:
    """Returns (max_width_pt, total_height_pt, line_count) for wrapped text."""
    if not text:
        return (0.0, 0.0, 0)

    font = None
    try:
        font = ImageFont.truetype(font_name, int(font_size_pt * 96 / 72))
    except Exception:
        try:
            font_file = f"C:/Windows/Fonts/{font_name.lower()}.ttf"
            font = ImageFont.truetype(font_file, int(font_size_pt * 96 / 72))
        except Exception:
            font = None

    lines: list[str] = []
    paragraphs = text.split("\n")
    for para in paragraphs:
        words = para.split(" ")
        current_line: list[str] = []
        for word in words:
            candidate = " ".join(current_line + [word])
            if font is not None:
                bbox = font.getbbox(candidate)
                w_pt = (bbox[2] - bbox[0]) * 72 / 96
            else:
                w_pt = len(candidate) * 0.52 * font_size_pt

            if w_pt <= max_width_pt or not current_line:
                current_line.append(word)
            else:
                lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))

    line_count = max(1, len(lines))
    line_height_pt = font_size_pt * 1.25
    total_height_pt = line_count * line_height_pt
    return (max_width_pt, total_height_pt, line_count)


def fit_font_size(
    text: str,
    font_name: str,
    base_pt: float,
    min_pt: float,
    box_w_emu: int,
    box_h_emu: int,
    slack: float = 0.08,
) -> tuple[Pt, bool]:
    """Finds largest font size in [min_pt, base_pt] that fits box_h_emu with 8% slack room."""
    box_w_pt = box_w_emu / 12700.0
    box_h_pt = box_h_emu / 12700.0
    usable_h_pt = box_h_pt * (1.0 - slack)

    curr_pt = base_pt
    while curr_pt >= min_pt:
        _, h_pt, _ = measure_text_box(text, font_name, curr_pt, box_w_pt)
        if h_pt <= usable_h_pt:
            return (Pt(curr_pt), True)
        curr_pt -= 0.5

    return (Pt(min_pt), False)


# --- PRIMITIVE DRAWING HELPERS ---


def add_soft_shadow(shape: Any) -> None:
    try:
        shadow_xml = (
            '<a:effectLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:outerShdw blurRad="76200" dist="25400" dir="5400000" algn="tl" rotWithShape="0">'
            '<a:srgbClr val="000000"><a:alpha val="18000"/></a:srgbClr>'
            "</a:outerShdw></a:effectLst>"
        )
        shape._element.spPr.append(parse_xml(shadow_xml))
    except Exception:
        pass


def apply_dashed_outline(shape: Any, color_rgb: RGBColor | None = None) -> None:
    try:
        spPr = shape._element.spPr
        ln = spPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ln")
        if ln is None:
            c_val = f"{color_rgb[0]:02X}{color_rgb[1]:02X}{color_rgb[2]:02X}" if color_rgb else "94A3B8"
            ln = parse_xml(
                f'<a:ln xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" w="19050">'
                f'<a:prstDash val="dash"/>'
                f'<a:solidFill><a:srgbClr val="{c_val}"/></a:solidFill>'
                f'</a:ln>'
            )
            spPr.append(ln)
        else:
            prstDash = ln.find("{http://schemas.openxmlformats.org/drawingml/2006/main}prstDash")
            if prstDash is None:
                ln.append(parse_xml('<a:prstDash xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" val="dash"/>'))
            else:
                prstDash.set("val", "dash")
    except Exception:
        pass


def add_card(
    slide: PptxSlide,
    box: Box,
    panel_color: str,
    accent_color: str,
    dashed: bool = False,
    top_bar: bool = True,
    tokens: ProfileTokens | None = None,
) -> Any:
    """Draws a rounded card with soft shadow, border tint, and optional top accent bar."""
    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, box.left, box.top, box.width, box.height
    )
    card.adjustments[0] = 0.05
    card.fill.solid()
    fill_hex = panel_color if not dashed else blend_with_white(panel_color, 0.4)
    card.fill.fore_color.rgb = hex_to_rgb(fill_hex)

    if dashed:
        card.line.color.rgb = hex_to_rgb(tokens.muted_text_color if tokens else "94A3B8")
        card.line.width = Pt(1.2)
        apply_dashed_outline(card, hex_to_rgb(tokens.muted_text_color if tokens else "94A3B8"))
    else:
        border_hex = blend_with_white(accent_color, 0.5) if (tokens and tokens.bg_lum == "light") else blend_with_black(accent_color, 0.4)
        card.line.color.rgb = hex_to_rgb(border_hex)
        card.line.width = Pt(0.75)
        add_soft_shadow(card)

    if top_bar and not dashed:
        bar_h = max(int(box.height * 0.035), 40000)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, box.left, box.top, box.width, bar_h)
        bar.fill.solid()
        bar.fill.fore_color.rgb = hex_to_rgb(accent_color)
        bar.line.fill.background()

    return card


def add_pill(
    slide: PptxSlide,
    box: Box,
    text: str,
    bg_color: str,
    text_color: str,
    tokens: ProfileTokens,
) -> Any:
    """Draws a rounded pill with uppercase bold text."""
    pill = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, box.left, box.top, box.width, box.height
    )
    pill.adjustments[0] = 0.5
    pill.fill.solid()
    pill.fill.fore_color.rgb = hex_to_rgb(bg_color)
    pill.line.fill.background()

    tf = pill.text_frame
    tf.word_wrap = False
    tf.margin_left = int(box.width * 0.05)
    tf.margin_right = int(box.width * 0.05)
    tf.margin_top = int(box.height * 0.05)
    tf.margin_bottom = int(box.height * 0.05)
    p = tf.paragraphs[0]
    p.text = text.upper()
    p.font.name = tokens.font_name
    pill_size_pt = max(tokens.min_body_pt * 0.85, 7.5)
    p.font.size = Pt(pill_size_pt)
    p.font.bold = True
    safe_text_color = ensure_contrast(text_color, bg_color, 4.5)
    p.font.color.rgb = hex_to_rgb(safe_text_color)
    p.alignment = PP_ALIGN.CENTER
    return pill


def add_number_badge(
    slide: PptxSlide,
    left: int,
    top: int,
    diameter: int,
    number_text: str,
    bg_color: str,
    tokens: ProfileTokens,
) -> Any:
    badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, left, top, diameter, diameter)
    badge.fill.solid()
    badge.fill.fore_color.rgb = hex_to_rgb(bg_color)
    badge.line.fill.background()

    tf = badge.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = int(diameter * 0.1)
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.text = str(number_text)
    p.font.name = tokens.font_name
    p.font.size = Pt(max(tokens.min_body_pt * 0.9, 8.0))
    p.font.bold = True
    safe_color = ensure_contrast("#FFFFFF", bg_color, 4.0)
    p.font.color.rgb = hex_to_rgb(safe_color)
    p.alignment = PP_ALIGN.CENTER
    return badge


def add_arrow_connector(
    slide: PptxSlide,
    box: Box,
    color: str = "9CA3AF",
) -> Any:
    arrow = slide.shapes.add_shape(
        MSO_SHAPE.RIGHT_ARROW, box.left, box.top, box.width, box.height
    )
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = hex_to_rgb(color)
    arrow.line.fill.background()
    return arrow


def populate_card_text(
    tf: Any,
    full_text: str,
    font_name: str,
    base_pt: float,
    min_pt: float,
    box_w: int,
    box_h: int,
    panel_color: str,
    text_color: str,
    muted_color: str,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_top = 0
    p = tf.paragraphs[0]

    font_size, fits = fit_font_size(full_text, font_name, base_pt, min_pt, box_w, box_h)
    if not fits and on_progress:
        try:
            on_progress(f"Tight fit on slide {slide_num}")
        except Exception:
            pass

    m = re.match(r"^([^:\.\-\n]{2,40}?)(:\s*|\s+–\s+|\s+-\s+|\.\s+)(.+)$", full_text, flags=re.DOTALL)
    if m:
        heading, sep, body = m.groups()
        p.text = ""
        r0 = p.add_run()
        r0.text = heading + sep
        r0.font.name = font_name
        r0.font.size = font_size
        r0.font.bold = True
        r0.font.color.rgb = hex_to_rgb(ensure_contrast(text_color, panel_color, 4.5))

        r1 = p.add_run()
        r1.text = body
        r1.font.name = font_name
        r1.font.size = font_size
        r1.font.bold = False
        r1.font.color.rgb = hex_to_rgb(ensure_contrast(muted_color, panel_color, 4.5))
    else:
        p.text = full_text
        p.font.name = font_name
        p.font.size = font_size
        p.font.color.rgb = hex_to_rgb(ensure_contrast(text_color, panel_color, 4.5))


def split_heading_body(text: str) -> tuple[str, str]:
    if ":" in text:
        parts = text.split(":", 1)
        return parts[0].strip(), parts[1].strip()
    if " - " in text:
        parts = text.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    if ". " in text:
        parts = text.split(". ", 1)
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


# --- SLIDE RECIPES (RELATIVE UNITS) ---


def render_title_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    is_custom: bool = False,
) -> None:
    """Title slide: custom template title layout inside box, or default dual-panel canvas."""
    if is_custom:
        title_h = int(box.height * 0.38)
        tb = slide.shapes.add_textbox(box.left, box.top, box.width, title_h)
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        p = tf.paragraphs[0]
        p.text = slide_model.title.text
        p.font.name = tokens.title_font_name
        p.font.size = Pt(max(26.0 * tokens.scale, 22.0))
        p.font.bold = True
        p.font.color.rgb = hex_to_rgb(ensure_contrast(tokens.primary, tokens.bg_color, 4.5))

        line_t = box.top + title_h + int(box.height * 0.02)
        line = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, box.left, line_t, int(box.width * 0.40), max(int(box.height * 0.015), 30000)
        )
        line.fill.solid()
        line.fill.fore_color.rgb = hex_to_rgb(tokens.secondary)
        line.line.fill.background()

        sub_text = slide_model.subtitle.text if slide_model.subtitle else ""
        if sub_text:
            s_top = line_t + int(box.height * 0.04)
            s_h = int(box.height * 0.35)
            tb_s = slide.shapes.add_textbox(box.left, s_top, box.width, s_h)
            tf_s = tb_s.text_frame
            tf_s.word_wrap = True
            tf_s.margin_left = 0
            tf_s.margin_top = 0
            ps = tf_s.paragraphs[0]
            ps.text = sub_text
            ps.font.name = tokens.font_name
            ps.font.size = Pt(max(15.0 * tokens.scale, 13.0))
            ps.font.color.rgb = hex_to_rgb(ensure_contrast(tokens.text_color, tokens.bg_color, 4.5))
        return

    left_w = int(box.width * 0.60)
    right_w = box.width - left_w

    left_panel = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, box.left, box.top, left_w, box.height
    )
    left_panel.fill.solid()
    left_panel.fill.fore_color.rgb = hex_to_rgb(tokens.primary)
    left_panel.line.fill.background()

    # Pill
    pill_w = int(left_w * 0.35)
    pill_h = int(box.height * 0.055)
    pill_l = box.left + int(left_w * 0.10)
    pill_t = box.top + int(box.height * 0.15)
    add_pill(
        slide,
        Box(pill_l, pill_t, pill_w, pill_h),
        "OVERVIEW",
        bg_color=tokens.tertiary,
        text_color=tokens.bg_color,
        tokens=tokens,
    )

    # Title
    t_box_l = pill_l
    t_box_t = pill_t + pill_h + int(box.height * 0.06)
    t_box_w = int(left_w * 0.82)
    t_box_h = int(box.height * 0.35)

    title_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 0), None)
    if title_ph:
        title_ph.left = t_box_l
        title_ph.top = t_box_t
        title_ph.width = t_box_w
        title_ph.height = t_box_h
        title_ph.text = slide_model.title.text
        tf = title_ph.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        for p in tf.paragraphs:
            p.font.name = tokens.title_font_name
            p.font.size = Pt(max(26.0 * tokens.scale, 20.0))
            p.font.bold = True
            safe_c = ensure_contrast("#FFFFFF", tokens.primary, 4.5)
            p.font.color.rgb = hex_to_rgb(safe_c)
    else:
        tb = slide.shapes.add_textbox(t_box_l, t_box_t, t_box_w, t_box_h)
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        p = tf.paragraphs[0]
        p.text = slide_model.title.text
        p.font.name = tokens.title_font_name
        p.font.size = Pt(max(26.0 * tokens.scale, 20.0))
        p.font.bold = True
        safe_c = ensure_contrast("#FFFFFF", tokens.primary, 4.5)
        p.font.color.rgb = hex_to_rgb(safe_c)

    # Accent line
    line_t = t_box_t + t_box_h + int(box.height * 0.02)
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, t_box_l, line_t, int(left_w * 0.40), int(box.height * 0.01)
    )
    line.fill.solid()
    line.fill.fore_color.rgb = hex_to_rgb(tokens.secondary)
    line.line.fill.background()

    # Subtitle
    sub_text = slide_model.subtitle.text if slide_model.subtitle else ""
    if sub_text:
        s_box_t = line_t + int(box.height * 0.03)
        s_box_h = int(box.height * 0.25)
        sub_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 1), None)
        if sub_ph:
            sub_ph.left = t_box_l
            sub_ph.top = s_box_t
            sub_ph.width = t_box_w
            sub_ph.height = s_box_h
            sub_ph.text = sub_text
            tf_s = sub_ph.text_frame
            tf_s.word_wrap = True
            tf_s.margin_left = 0
            tf_s.margin_top = 0
            for p_s in tf_s.paragraphs:
                p_s.font.name = tokens.font_name
                p_s.font.size = Pt(max(14.0 * tokens.scale, 11.0))
                p_s.font.color.rgb = hex_to_rgb(blend_with_white(tokens.primary, 0.7))
        else:
            tb_s = slide.shapes.add_textbox(t_box_l, s_box_t, t_box_w, s_box_h)
            tf_s = tb_s.text_frame
            tf_s.word_wrap = True
            tf_s.margin_left = 0
            tf_s.margin_top = 0
            p_s = tf_s.paragraphs[0]
            p_s.text = sub_text
            p_s.font.name = tokens.font_name
            p_s.font.size = Pt(max(14.0 * tokens.scale, 11.0))
            p_s.font.color.rgb = hex_to_rgb(blend_with_white(tokens.primary, 0.7))

    # Right decorative area
    right_l = box.left + left_w
    right_bg = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, right_l, box.top, right_w, box.height
    )
    right_bg.fill.solid()
    right_bg.fill.fore_color.rgb = hex_to_rgb(tokens.panel_fills[4] if len(tokens.panel_fills) > 4 else tokens.bg_color)
    right_bg.line.fill.background()

    # Decorative shapes inside right panel
    r1_w = int(right_w * 0.70)
    r1_h = int(box.height * 0.35)
    r1 = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        right_l + int(right_w * 0.15),
        box.top + int(box.height * 0.15),
        r1_w,
        r1_h,
    )
    r1.adjustments[0] = 0.08
    r1.fill.solid()
    r1.fill.fore_color.rgb = hex_to_rgb(tokens.panel_fills[0])
    r1.line.color.rgb = hex_to_rgb(tokens.secondary)
    r1.line.width = Pt(1.5)
    add_soft_shadow(r1)

    c1_d = int(min(right_w * 0.35, box.height * 0.25))
    c1 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        right_l + int(right_w * 0.45),
        box.top + int(box.height * 0.45),
        c1_d,
        c1_d,
    )
    c1.fill.solid()
    c1.fill.fore_color.rgb = hex_to_rgb(tokens.tertiary)
    c1.line.fill.background()
    add_soft_shadow(c1)


def render_problem_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    if not bullets:
        return

    if len(bullets) == 1:
        card_boxes = [box]
        b_list = bullets
    else:
        strip_h = int(box.height * 0.22)
        strip_box = Box(box.left, box.top, box.width, strip_h)
        first_b = bullets[0]
        is_ni = (first_b.source == Source.NEEDS_INPUT)

        add_card(
            slide,
            strip_box,
            panel_color=tokens.panel_fills[0],
            accent_color=tokens.primary,
            dashed=is_ni,
            top_bar=False,
            tokens=tokens,
        )

        pill_w = max(int(strip_box.width * 0.12), 600000)
        pill_h = max(int(strip_h * 0.40), 200000)
        pill_l = strip_box.left + tokens.padding
        pill_t = strip_box.top + int((strip_h - pill_h) / 2)
        add_pill(
            slide,
            Box(pill_l, pill_t, pill_w, pill_h),
            "PROBLEM",
            bg_color=tokens.primary,
            text_color=tokens.bg_color,
            tokens=tokens,
        )

        txt_l = pill_l + pill_w + tokens.padding
        txt_w = strip_box.right - txt_l - tokens.padding
        tb = slide.shapes.add_textbox(txt_l, pill_t, txt_w, pill_h)
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        p = tf.paragraphs[0]
        p.text = first_b.text
        p.font.name = tokens.font_name
        p_size, ok = fit_font_size(first_b.text, tokens.font_name, tokens.base_body_pt, tokens.min_body_pt, txt_w, pill_h)
        p.font.size = p_size
        safe_c = ensure_contrast(tokens.text_color, tokens.panel_fills[0], 4.5)
        p.font.color.rgb = hex_to_rgb(safe_c)

        lower_t = strip_box.bottom + tokens.gap
        lower_h = box.bottom - lower_t
        lower_box = Box(box.left, lower_t, box.width, lower_h)
        b_list = bullets[1:]
        card_boxes = place_grid(len(b_list), lower_box, policy="auto", gap_x=tokens.gap, gap_y=tokens.gap)

    for i, (b, cb) in enumerate(zip(b_list, card_boxes, strict=False)):
        is_ni = (b.source == Source.NEEDS_INPUT)
        accent = tokens.accents[(i + 1) % len(tokens.accents)]
        panel = tokens.panel_fills[(i + 1) % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        badge_d = max(int(min(cb.width, cb.height) * 0.18), 240000)
        b_badge_l = cb.left + tokens.padding
        b_badge_t = cb.top + tokens.padding + int(cb.height * 0.04)
        add_number_badge(slide, b_badge_l, b_badge_t, badge_d, str(i + 1), accent, tokens)

        tb_t = b_badge_t + badge_d + int(tokens.padding * 0.5)
        tb_w = cb.width - 2 * tokens.padding
        tb_h = cb.bottom - tb_t - tokens.padding

        tb = slide.shapes.add_textbox(cb.left + tokens.padding, tb_t, tb_w, tb_h)
        tf = tb.text_frame
        populate_card_text(
            tf,
            b.text,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            tb_w,
            tb_h,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )


def render_solution_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    if not bullets:
        return

    n = len(bullets)
    policy = "horizontal" if (box.aspect_ratio >= 1.2 and n <= 4) else "auto"
    card_boxes = place_grid(n, box, policy=policy, gap_x=int(tokens.gap * 1.4), gap_y=tokens.gap)

    for i, (b, cb) in enumerate(zip(bullets, card_boxes, strict=False)):
        is_ni = (b.source == Source.NEEDS_INPUT)
        accent = tokens.accents[i % len(tokens.accents)]
        panel = tokens.panel_fills[i % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        # Number badge
        badge_d = max(int(min(cb.width, cb.height) * 0.16), 240000)
        badge_l = cb.left + tokens.padding
        badge_t = cb.top + tokens.padding + int(cb.height * 0.03)
        add_number_badge(slide, badge_l, badge_t, badge_d, str(i + 1), accent, tokens)

        tb_t = badge_t + badge_d + int(tokens.padding * 0.5)
        tb_w = cb.width - 2 * tokens.padding
        tb_h = cb.bottom - tb_t - tokens.padding

        tb = slide.shapes.add_textbox(cb.left + tokens.padding, tb_t, tb_w, tb_h)
        tf = tb.text_frame
        populate_card_text(
            tf,
            b.text,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            tb_w,
            tb_h,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )

        # Draw arrow connector between horizontal cards
        if policy == "horizontal" and i < n - 1:
            next_cb = card_boxes[i + 1]
            arrow_w = next_cb.left - cb.right
            arrow_h = int(cb.height * 0.10)
            arrow_l = cb.right
            arrow_t = cb.top + int((cb.height - arrow_h) / 2)
            add_arrow_connector(slide, Box(arrow_l, arrow_t, arrow_w, arrow_h), color="9CA3AF")


def render_features_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    if not bullets:
        return

    n = len(bullets)
    card_boxes = place_grid(n, box, policy="auto", gap_x=tokens.gap, gap_y=tokens.gap)

    for i, (b, cb) in enumerate(zip(bullets, card_boxes, strict=False)):
        is_ni = (b.source == Source.NEEDS_INPUT)
        accent = tokens.accents[i % len(tokens.accents)]
        panel = tokens.panel_fills[i % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        pill_w = max(int(cb.width * 0.35), 450000)
        pill_h = max(int(cb.height * 0.16), 160000)
        pill_l = cb.left + tokens.padding
        pill_t = cb.top + tokens.padding + int(cb.height * 0.03)
        add_pill(
            slide,
            Box(pill_l, pill_t, pill_w, pill_h),
            f"FEATURE {i + 1}",
            bg_color=accent,
            text_color=tokens.bg_color,
            tokens=tokens,
        )

        tb_t = pill_t + pill_h + int(tokens.padding * 0.4)
        tb_w = cb.width - 2 * tokens.padding
        tb_h = cb.bottom - tb_t - tokens.padding

        tb = slide.shapes.add_textbox(cb.left + tokens.padding, tb_t, tb_w, tb_h)
        tf = tb.text_frame
        populate_card_text(
            tf,
            b.text,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            tb_w,
            tb_h,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )


def render_architecture_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    diagram_png: Path | None = None,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    has_diagram = diagram_png is not None and Path(diagram_png).exists()

    if has_diagram:
        # Give the architecture diagram a generous, prominent 58% width
        right_w = int(box.width * 0.58)
        left_w = box.width - right_w - tokens.gap
        left_box = Box(box.left, box.top, left_w, box.height)
        right_box = Box(box.left + left_w + tokens.gap, box.top, right_w, box.height)
    else:
        left_box = box
        right_box = None

    if bullets:
        card_boxes = place_grid(len(bullets), left_box, policy="vertical", gap_x=0, gap_y=tokens.gap)
        for i, (b, cb) in enumerate(zip(bullets, card_boxes, strict=False)):
            is_ni = (b.source == Source.NEEDS_INPUT)
            accent = tokens.accents[i % len(tokens.accents)]
            panel = tokens.panel_fills[i % len(tokens.panel_fills)]

            add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=False, tokens=tokens)

            badge_d = max(int(min(cb.width, cb.height) * 0.22), 220000)
            add_number_badge(slide, cb.left + tokens.padding, cb.top + tokens.padding, badge_d, str(i + 1), accent, tokens)

            tb_l = cb.left + tokens.padding + badge_d + tokens.padding
            tb_w = cb.right - tb_l - tokens.padding
            tb_h = cb.height - 2 * tokens.padding
            tb = slide.shapes.add_textbox(tb_l, cb.top + tokens.padding, tb_w, tb_h)
            tf = tb.text_frame
            populate_card_text(
                tf,
                b.text,
                tokens.font_name,
                tokens.base_body_pt,
                tokens.min_body_pt,
                tb_w,
                tb_h,
                panel_color=panel,
                text_color=tokens.text_color,
                muted_color=tokens.muted_text_color,
                on_progress=on_progress,
                slide_num=slide_num,
            )

    if right_box and diagram_png:
        # Prominent card container for diagram
        add_card(slide, right_box, panel_color=tokens.panel_fills[0], accent_color=tokens.primary, dashed=False, top_bar=True, tokens=tokens)

        # Subtle pill badge on diagram container
        pill_w = max(int(right_box.width * 0.42), 480000)
        pill_h = max(int(right_box.height * 0.08), 160000)
        pill_l = right_box.left + tokens.padding
        pill_t = right_box.top + tokens.padding
        add_pill(
            slide,
            Box(pill_l, pill_t, pill_w, pill_h),
            "ARCHITECTURE FLOW",
            bg_color=tokens.primary,
            text_color=tokens.bg_color,
            tokens=tokens,
        )

        try:
            from PIL import Image as PILImage

            with PILImage.open(str(diagram_png)) as img:
                img_w, img_h = img.size
            if img_w > 0 and img_h > 0:
                pad_x = tokens.padding
                pad_top = pill_h + int(tokens.padding * 1.2)
                pad_bot = tokens.padding
                max_w = right_box.width - 2 * pad_x
                max_h = right_box.height - pad_top - pad_bot
                sc = min(max_w / img_w, max_h / img_h)
                pic_w = int(img_w * sc)
                pic_h = int(img_h * sc)
                pic_l = right_box.left + pad_x + int((max_w - pic_w) / 2)
                pic_t = right_box.top + pad_top + int((max_h - pic_h) / 2)
                slide.shapes.add_picture(str(diagram_png), pic_l, pic_t, pic_w, pic_h)
        except Exception:
            pass


def render_tech_stack_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    if not bullets:
        return

    n = len(bullets)
    card_boxes = place_grid(n, box, policy="auto", gap_x=tokens.gap, gap_y=tokens.gap)

    for i, (b, cb) in enumerate(zip(bullets, card_boxes, strict=False)):
        is_ni = (b.source == Source.NEEDS_INPUT)
        accent = tokens.accents[i % len(tokens.accents)]
        panel = tokens.panel_fills[i % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        pill_w = max(int(cb.width * 0.40), 500000)
        pill_h = max(int(cb.height * 0.16), 160000)
        pill_l = cb.left + tokens.padding
        pill_t = cb.top + tokens.padding + int(cb.height * 0.03)
        add_pill(
            slide,
            Box(pill_l, pill_t, pill_w, pill_h),
            "STACK",
            bg_color=accent,
            text_color=tokens.bg_color,
            tokens=tokens,
        )

        tb_t = pill_t + pill_h + int(tokens.padding * 0.4)
        tb_w = cb.width - 2 * tokens.padding
        tb_h = cb.bottom - tb_t - tokens.padding

        tb = slide.shapes.add_textbox(cb.left + tokens.padding, tb_t, tb_w, tb_h)
        tf = tb.text_frame
        populate_card_text(
            tf,
            b.text,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            tb_w,
            tb_h,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )


def render_demo_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    diagram_png: Path | None = None,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    # Demo slide shares architecture split recipe
    render_architecture_slide(slide, slide_model, box, tokens, diagram_png, on_progress, slide_num)


def render_impact_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    if not bullets:
        return

    n = len(bullets)
    card_boxes = place_grid(n, box, policy="auto", gap_x=tokens.gap, gap_y=tokens.gap)

    for i, (b, cb) in enumerate(zip(bullets, card_boxes, strict=False)):
        is_ni = (b.source == Source.NEEDS_INPUT)
        accent = tokens.accents[i % len(tokens.accents)]
        panel = tokens.panel_fills[i % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        # Check for numeric / metric token (e.g. 10x, 99.9%, <1ms)
        tb = slide.shapes.add_textbox(cb.left + tokens.padding, cb.top + tokens.padding + int(cb.height * 0.05), cb.width - 2 * tokens.padding, cb.height - 2 * tokens.padding)
        tf = tb.text_frame
        populate_card_text(
            tf,
            b.text,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            cb.width - 2 * tokens.padding,
            cb.height - 2 * tokens.padding,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )


def render_roadmap_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    if not bullets:
        return

    n = len(bullets)
    policy = "horizontal" if (box.aspect_ratio >= 1.2 and n <= 4) else "auto"
    card_boxes = place_grid(n, box, policy=policy, gap_x=int(tokens.gap * 1.4), gap_y=tokens.gap)

    for i, (b, cb) in enumerate(zip(bullets, card_boxes, strict=False)):
        is_ni = (b.source == Source.NEEDS_INPUT)
        accent = tokens.accents[i % len(tokens.accents)]
        panel = tokens.panel_fills[i % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        pill_w = max(int(cb.width * 0.45), 500000)
        pill_h = max(int(cb.height * 0.16), 160000)
        pill_l = cb.left + tokens.padding
        pill_t = cb.top + tokens.padding + int(cb.height * 0.03)
        add_pill(
            slide,
            Box(pill_l, pill_t, pill_w, pill_h),
            f"PHASE {i + 1}",
            bg_color=accent,
            text_color=tokens.bg_color,
            tokens=tokens,
        )

        tb_t = pill_t + pill_h + int(tokens.padding * 0.4)
        tb_w = cb.width - 2 * tokens.padding
        tb_h = cb.bottom - tb_t - tokens.padding

        tb = slide.shapes.add_textbox(cb.left + tokens.padding, tb_t, tb_w, tb_h)
        tf = tb.text_frame
        populate_card_text(
            tf,
            b.text,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            tb_w,
            tb_h,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )

        if policy == "horizontal" and i < n - 1:
            next_cb = card_boxes[i + 1]
            arrow_w = next_cb.left - cb.right
            arrow_h = int(cb.height * 0.10)
            arrow_l = cb.right
            arrow_t = cb.top + int((cb.height - arrow_h) / 2)
            add_arrow_connector(slide, Box(arrow_l, arrow_t, arrow_w, arrow_h), color="9CA3AF")


def render_closing_slide(
    slide: PptxSlide,
    slide_model: Slide,
    box: Box,
    tokens: ProfileTokens,
    on_progress: Any = None,
    slide_num: int = 1,
) -> None:
    bullets = slide_model.bullets
    n = max(1, len(bullets))
    card_boxes = place_grid(n, box, policy="auto", gap_x=tokens.gap, gap_y=tokens.gap)

    for i, cb in enumerate(card_boxes):
        b = bullets[i] if i < len(bullets) else None
        is_ni = (b.source == Source.NEEDS_INPUT) if b else False
        accent = tokens.accents[i % len(tokens.accents)]
        panel = tokens.panel_fills[i % len(tokens.panel_fills)]

        add_card(slide, cb, panel_color=panel, accent_color=accent, dashed=is_ni, top_bar=True, tokens=tokens)

        badge_d = max(int(min(cb.width, cb.height) * 0.18), 240000)
        add_number_badge(slide, cb.left + tokens.padding, cb.top + tokens.padding + int(cb.height * 0.03), badge_d, str(i + 1), accent, tokens)

        text_content = b.text if b else "Thank You: Questions & Next Steps"
        tb_t = cb.top + tokens.padding + badge_d + int(tokens.padding * 0.4)
        tb_w = cb.width - 2 * tokens.padding
        tb_h = cb.bottom - tb_t - tokens.padding

        tb = slide.shapes.add_textbox(cb.left + tokens.padding, tb_t, tb_w, tb_h)
        tf = tb.text_frame
        populate_card_text(
            tf,
            text_content,
            tokens.font_name,
            tokens.base_body_pt,
            tokens.min_body_pt,
            tb_w,
            tb_h,
            panel_color=panel,
            text_color=tokens.text_color,
            muted_color=tokens.muted_text_color,
            on_progress=on_progress,
            slide_num=slide_num,
        )


def choose_recipe_for_slot(slot_title: str, slide_model: Slide) -> str:
    st_lower = slot_title.lower()
    if any(k in st_lower for k in ("solution", "proposed")):
        return "solution"
    if any(k in st_lower for k in ("approach", "architecture", "technical", "stack")):
        return "architecture"
    if any(k in st_lower for k in ("feasib", "viabil", "risk")):
        return "problem"
    if any(k in st_lower for k in ("impact", "benefit", "metric", "kpi", "roi")):
        return "impact"
    if any(k in st_lower for k in ("research", "reference", "source", "link")):
        return "features"
    if any(k in st_lower for k in ("team", "problem", "challenge")):
        return "problem"
    return slide_model.type.value if hasattr(slide_model.type, "value") else str(slide_model.type)


def is_all_needs_input(slide: Slide) -> bool:
    """Helper preserved for compatibility. Drops no slides in UPGRADE-3."""
    return False


# Constants retained for compatibility
SLIDE_WIDTH_EMU = 12192000
SLIDE_HEIGHT_EMU = 6858000
SLIDE_WIDTH_IN = 13.333333
SLIDE_HEIGHT_IN = 7.5


def render_designed_slide(
    slide_model: Slide,
    prs: Presentation,
    pptx_slide: PptxSlide,
    diagram_png: Path | None = None,
    project_name: str = "",
    slide_num: int = 1,
    total_slides: int = 1,
    custom_body_rect: tuple[int, int, int, int] | None = None,
    profile: TemplateProfile | None = None,
    slot_title: str | None = None,
    on_progress: Any = None,
) -> None:
    """Renders a slide using profile tokens and responsive layout inside custom_body_rect or content box."""
    if profile is None:
        from app.services.template_inspector import analyze_template

        # Synthesize fallback profile from presentation size
        profile = TemplateProfile(
            slide_width=int(prs.slide_width),
            slide_height=int(prs.slide_height),
            scale=prs.slide_height / 6858000.0,
            mode="layouts",
        )

    # Determine content Box
    if custom_body_rect is not None:
        content_box = Box(*custom_body_rect)
    else:
        margin_x = int(0.04 * prs.slide_width)
        content_box = Box(
            left=margin_x,
            top=int(0.20 * prs.slide_height),
            width=int(prs.slide_width - 2 * margin_x),
            height=int(0.72 * prs.slide_height),
        )

    tokens = ProfileTokens.from_profile(profile, box=content_box)

    recipe_key = choose_recipe_for_slot(slot_title, slide_model) if slot_title else slide_model.type.value

    is_custom = bool(custom_body_rect is not None or (profile and profile.mode == "fill"))
    if recipe_key == "title":
        render_title_slide(pptx_slide, slide_model, content_box, tokens, is_custom=is_custom)
    elif recipe_key == "problem":
        render_problem_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    elif recipe_key == "solution":
        render_solution_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    elif recipe_key == "features":
        render_features_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    elif recipe_key == "architecture":
        render_architecture_slide(pptx_slide, slide_model, content_box, tokens, diagram_png, on_progress, slide_num)
    elif recipe_key == "tech_stack":
        render_tech_stack_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    elif recipe_key == "demo":
        render_demo_slide(pptx_slide, slide_model, content_box, tokens, diagram_png, on_progress, slide_num)
    elif recipe_key == "impact":
        render_impact_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    elif recipe_key == "roadmap":
        render_roadmap_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    elif recipe_key == "closing":
        render_closing_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
    else:
        render_features_slide(pptx_slide, slide_model, content_box, tokens, on_progress, slide_num)
