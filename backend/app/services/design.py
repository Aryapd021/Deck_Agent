"""Professional design engine for PowerPoint presentations.

Implements modern Gamma-style slide recipes, typography, color palette,
shadows, badges, pills, and dynamic text fitting.
"""

from collections.abc import Callable
import math
from pathlib import Path
import re
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml import parse_xml
from pptx.slide import Slide as PptxSlide
from pptx.util import Inches, Pt

from app.models.deck import Deck, Field, Slide, SlideType, Source

# --- DESIGN CONSTANTS ---
FONT_FAMILY = "Calibri"

TITLE_PT = Pt(28)
SECTION_LABEL_PT = Pt(11)
CARD_HEADING_PT = Pt(12)
CARD_BODY_PT = Pt(11)
BADGE_PT = Pt(10)
PILL_PT = Pt(9)
KPI_PT = Pt(32)
MIN_FONT_PT = Pt(10)

# 16:9 Canvas Dimensions (13.333 x 7.5 in)
SLIDE_WIDTH_EMU = 12192000
SLIDE_HEIGHT_EMU = 6858000
SLIDE_WIDTH_IN = 13.333333
SLIDE_HEIGHT_IN = 7.5

# Palette Colors (sampled from reference deck)
COLOR_NAVY = "1F3A6E"
COLOR_BLUE = "006FC0"
COLOR_PURPLE = "6B4FA0"
COLOR_ORANGE = "F28C28"
COLOR_GREEN = "2E9E5B"
COLOR_TEAL = "1A9C8F"
COLOR_GOLD = "D9A441"
COLOR_RED = "C0392B"

# Panel Fills
PANEL_LIGHT_BLUE = "ECF3FB"
PANEL_NEUTRAL = "F6F7F9"
PANEL_LAVENDER = "F5F4FA"
PANEL_PEACH = "FDF1E3"
PANEL_MINT = "E8F5EE"
PANEL_WHITE = "FFFFFF"

# Text Colors
COLOR_TEXT_DARK = "1F2937"
COLOR_TEXT_MUTED = "6B7280"
COLOR_WHITE = "FFFFFF"

ACCENTS = [
    COLOR_BLUE,
    COLOR_PURPLE,
    COLOR_TEAL,
    COLOR_ORANGE,
    COLOR_GREEN,
    COLOR_GOLD,
    COLOR_RED,
]

PANELS = [
    PANEL_LIGHT_BLUE,
    PANEL_LAVENDER,
    PANEL_MINT,
    PANEL_PEACH,
    PANEL_NEUTRAL,
]

# Content Area Bounds
CONTENT_LEFT = Inches(0.5)
CONTENT_TOP = Inches(1.45)
CONTENT_WIDTH = Inches(12.333)
CONTENT_HEIGHT = Inches(5.45)  # y: 1.45in to 6.90in


def hex_to_rgb(hex_str: str) -> RGBColor:
    clean = hex_str.lstrip("#")
    return RGBColor(int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))


def blend_with_white(hex_str: str, factor: float) -> str:
    """Blends color toward white (factor 0.0 = original, 1.0 = pure white)."""
    clean = hex_str.lstrip("#")
    r = int(clean[0:2], 16)
    g = int(clean[2:4], 16)
    b = int(clean[4:6], 16)
    r_blended = int(r + (255 - r) * factor)
    g_blended = int(g + (255 - g) * factor)
    b_blended = int(b + (255 - b) * factor)
    return f"{r_blended:02X}{g_blended:02X}{b_blended:02X}"


def get_contrast_color(bg_hex: str) -> str:
    """Returns dark or white text color ensuring contrast ratio >= 4.5:1."""
    clean = bg_hex.lstrip("#")
    r = int(clean[0:2], 16) / 255.0
    g = int(clean[2:4], 16) / 255.0
    b = int(clean[4:6], 16) / 255.0
    # Relative luminance
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return COLOR_WHITE if lum < 0.4 else COLOR_TEXT_DARK


def add_soft_shadow(shape: Any) -> None:
    """Appends soft outer shadow XML to a shape."""
    try:
        shadow_xml = (
            '<a:effectLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:outerShdw blurRad="76200" dist="25400" dir="5400000" algn="tl" rotWithShape="0">'
            '<a:srgbClr val="000000"><a:alpha val="20000"/></a:srgbClr>'
            "</a:outerShdw></a:effectLst>"
        )
        shape._element.spPr.append(parse_xml(shadow_xml))
    except Exception:
        pass


def estimate_font_size(
    text: str,
    width_in: float,
    height_in: float,
    default_pt: float = 11.0,
    min_pt: float = 10.0,
) -> Pt:
    """Estimates font size to fit text inside box with 10% slack and word wrap."""
    if not text:
        return Pt(default_pt)
    # Average character width at 11pt in Calibri is ~0.065 inches
    # Line height at 11pt is ~0.20 inches
    char_count = len(text)
    usable_w = max(0.5, width_in * 0.9)
    usable_h = max(0.5, height_in * 0.9)

    for pt in [default_pt, default_pt - 0.5, min_pt]:
        scale = pt / 11.0
        avg_char_w = 0.065 * scale
        line_h = 0.22 * scale
        chars_per_line = max(1, int(usable_w / avg_char_w))
        lines_needed = max(1, math.ceil(char_count / chars_per_line))
        total_h_needed = lines_needed * line_h
        if total_h_needed <= usable_h:
            return Pt(pt)
    return Pt(min_pt)


def clean_needs_input_text(text: str) -> tuple[bool, str]:
    """Detects and strips 'TODO:' or 'needs_input' markers, returning (is_needs_input, clean_hint)."""
    t = text.strip()
    is_ni = False
    upper = t.upper()
    if upper.startswith("TODO:") or upper.startswith("TODO"):
        is_ni = True
        t = re.sub(r"(?i)^todo:?\s*", "", t).strip()
    elif upper.startswith("ADD YOUR OWN:") or upper.startswith("NEEDS_INPUT:"):
        is_ni = True
        t = re.sub(r"(?i)^(add your own|needs_input):?\s*", "", t).strip()

    # Ensure no 'TODO' text ever leaks into rendered slide runs
    if "TODO" in t:
        t = re.sub(r"\bTODO:?\s*", "", t).strip()
        t = re.sub(r"\s+", " ", t)

    return is_ni, t


def split_heading_body(text: str) -> tuple[str, str]:
    """Splits text into (heading, body) using delimiters like ':', ' - ', or '. '."""
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


# --- PRIMITIVE DRAWING HELPERS ---


def add_card(
    slide: PptxSlide,
    left: int,
    top: int,
    width: int,
    height: int,
    panel_color: str = PANEL_WHITE,
    accent_color: str = COLOR_BLUE,
    dashed: bool = False,
    top_bar: bool = True,
) -> Any:
    """Draws a rounded card with soft shadow, border tint, and optional top accent bar."""
    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height
    )
    card.adjustments[0] = 0.06
    card.fill.solid()
    card.fill.fore_color.rgb = hex_to_rgb(panel_color)

    if dashed:
        card.line.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        card.line.width = Pt(1.0)
        # python-pptx doesn't have native dashed line enum, but muted line works cleanly
    else:
        border_hex = blend_with_white(accent_color, 0.5)
        card.line.color.rgb = hex_to_rgb(border_hex)
        card.line.width = Pt(0.75)

    add_soft_shadow(card)

    if top_bar and not dashed:
        bar_h = Inches(0.07)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, bar_h)
        bar.fill.solid()
        bar.fill.fore_color.rgb = hex_to_rgb(accent_color)
        bar.line.fill.background()

    return card


def add_pill(
    slide: PptxSlide,
    left: int,
    top: int,
    width: int,
    height: int,
    text: str,
    bg_color: str = COLOR_BLUE,
    text_color: str = COLOR_WHITE,
) -> Any:
    """Draws a fully rounded pill with uppercase bold text."""
    pill = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    pill.adjustments[0] = 0.5
    pill.fill.solid()
    pill.fill.fore_color.rgb = hex_to_rgb(bg_color)
    pill.line.fill.background()

    tf = pill.text_frame
    tf.word_wrap = False
    tf.margin_left = Inches(0.05)
    tf.margin_right = Inches(0.05)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    p = tf.paragraphs[0]
    p.text = text.upper()
    p.font.name = FONT_FAMILY
    p.font.size = PILL_PT
    p.font.bold = True
    p.font.color.rgb = hex_to_rgb(text_color)
    p.alignment = PP_ALIGN.CENTER
    return pill


def add_number_badge(
    slide: PptxSlide,
    left: int,
    top: int,
    diameter: int,
    number_text: str,
    bg_color: str = COLOR_BLUE,
) -> Any:
    """Draws a 0.32in circle number badge."""
    badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, left, top, diameter, diameter)
    badge.fill.solid()
    badge.fill.fore_color.rgb = hex_to_rgb(bg_color)
    badge.line.fill.background()

    tf = badge.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.text = str(number_text)
    p.font.name = FONT_FAMILY
    p.font.size = BADGE_PT
    p.font.bold = True
    p.font.color.rgb = hex_to_rgb(COLOR_WHITE)
    p.alignment = PP_ALIGN.CENTER
    return badge


def add_arrow_connector(
    slide: PptxSlide,
    left: int,
    top: int,
    width: int,
    height: int,
    color: str = "9CA3AF",
) -> Any:
    """Draws a right-pointing arrow shape between cards."""
    arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, left, top, width, height)
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = hex_to_rgb(color)
    arrow.line.fill.background()
    return arrow


# --- STANDARD SLIDE CHROME ---


def setup_slide_chrome(
    slide: PptxSlide,
    title_text: str,
    project_name: str,
    slide_num: int,
    total_slides: int,
    is_title_slide: bool = False,
) -> None:
    """Renders the standard white background, title, 0.06in accent line, and 0.3in blue footer bar."""
    if is_title_slide:
        return

    # White slide background
    try:
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = hex_to_rgb(COLOR_WHITE)
    except Exception:
        pass

    # Position Title placeholder or title text shape
    title_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 0), None)
    if title_ph:
        title_ph.left = Inches(0.5)
        title_ph.top = Inches(0.35)
        title_ph.width = Inches(12.333)
        title_ph.height = Inches(0.7)
        title_ph.text = title_text
        tf = title_ph.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        for p in tf.paragraphs:
            p.font.name = FONT_FAMILY
            p.font.size = TITLE_PT
            p.font.bold = True
            p.font.color.rgb = hex_to_rgb(COLOR_NAVY)
            p.alignment = PP_ALIGN.LEFT
    else:
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.35), Inches(12.333), Inches(0.7))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.name = FONT_FAMILY
        p.font.size = TITLE_PT
        p.font.bold = True
        p.font.color.rgb = hex_to_rgb(COLOR_NAVY)

    # 0.06in accent line under title
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0.5), Inches(1.15), Inches(12.333), Inches(0.06)
    )
    line.fill.solid()
    line.fill.fore_color.rgb = hex_to_rgb(COLOR_BLUE)
    line.line.fill.background()

    # 0.3in full-width blue footer bar at bottom
    footer = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, Inches(7.2), Inches(SLIDE_WIDTH_IN), Inches(0.3)
    )
    footer.fill.solid()
    footer.fill.fore_color.rgb = hex_to_rgb(COLOR_BLUE)
    footer.line.fill.background()

    # Project name on left
    tb_left = slide.shapes.add_textbox(Inches(0.5), Inches(7.2), Inches(6.0), Inches(0.3))
    tf_l = tb_left.text_frame
    tf_l.word_wrap = False
    tf_l.margin_left = 0
    tf_l.margin_top = Inches(0.04)
    p_l = tf_l.paragraphs[0]
    p_l.text = project_name
    p_l.font.name = FONT_FAMILY
    p_l.font.size = PILL_PT
    p_l.font.color.rgb = hex_to_rgb(COLOR_WHITE)

    # Slide number on right
    tb_right = slide.shapes.add_textbox(Inches(7.0), Inches(7.2), Inches(5.833), Inches(0.3))
    tf_r = tb_right.text_frame
    tf_r.word_wrap = False
    tf_r.margin_right = 0
    tf_r.margin_top = Inches(0.04)
    p_r = tf_r.paragraphs[0]
    p_r.text = f"{slide_num} / {total_slides}"
    p_r.font.name = FONT_FAMILY
    p_r.font.size = PILL_PT
    p_r.font.color.rgb = hex_to_rgb(COLOR_WHITE)
    p_r.alignment = PP_ALIGN.RIGHT


# --- SLIDE RECIPES ---


def render_title_slide(
    slide: PptxSlide,
    slide_model: Slide,
    project_name: str,
) -> None:
    """Title slide: left 60% navy panel, white 40pt bold title, light blue 18pt subtitle, gold accent line, right 40% decorative shapes."""
    # Left 60% navy panel (width = 8.0in, height = 7.5in)
    left_panel = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, Inches(8.0), Inches(7.5)
    )
    left_panel.fill.solid()
    left_panel.fill.fore_color.rgb = hex_to_rgb(COLOR_NAVY)
    left_panel.line.fill.background()

    # Pill reading 'HACKATHON PROJECT' or project name
    pill_text = project_name if project_name else "HACKATHON PROJECT"
    add_pill(
        slide,
        Inches(0.8),
        Inches(1.2),
        Inches(2.2),
        Inches(0.32),
        pill_text,
        bg_color=COLOR_GOLD,
        text_color=COLOR_NAVY,
    )

    # Title in white 40pt bold
    title_text = slide_model.title.text
    title_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 0), None)
    if title_ph:
        title_ph.left = Inches(0.8)
        title_ph.top = Inches(1.8)
        title_ph.width = Inches(6.4)
        title_ph.height = Inches(2.2)
        title_ph.text = title_text
        tf = title_ph.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        for p in tf.paragraphs:
            p.font.name = FONT_FAMILY
            p.font.size = Pt(40)
            p.font.bold = True
            p.font.color.rgb = hex_to_rgb(COLOR_WHITE)
    else:
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(6.4), Inches(2.2))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.name = FONT_FAMILY
        p.font.size = Pt(40)
        p.font.bold = True
        p.font.color.rgb = hex_to_rgb(COLOR_WHITE)

    # Gold accent line
    gold_line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(4.3), Inches(3.5), Inches(0.06)
    )
    gold_line.fill.solid()
    gold_line.fill.fore_color.rgb = hex_to_rgb(COLOR_GOLD)
    gold_line.line.fill.background()

    # Subtitle in light blue 18pt
    sub_text = slide_model.subtitle.text if slide_model.subtitle else ""
    sub_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 1), None)
    if sub_ph and sub_text:
        sub_ph.left = Inches(0.8)
        sub_ph.top = Inches(4.6)
        sub_ph.width = Inches(6.4)
        sub_ph.height = Inches(1.8)
        sub_ph.text = sub_text
        tf = sub_ph.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        for p in tf.paragraphs:
            p.font.name = FONT_FAMILY
            p.font.size = Pt(18)
            p.font.color.rgb = hex_to_rgb(PANEL_LIGHT_BLUE)
    elif sub_text:
        tb_sub = slide.shapes.add_textbox(Inches(0.8), Inches(4.6), Inches(6.4), Inches(1.8))
        tf = tb_sub.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        p = tf.paragraphs[0]
        p.text = sub_text
        p.font.name = FONT_FAMILY
        p.font.size = Pt(18)
        p.font.color.rgb = hex_to_rgb(PANEL_LIGHT_BLUE)

    # Right 40% decorative geometric shapes in accent colors (no images)
    # Background panel for right 40%
    right_bg = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(8.0), 0, Inches(5.333), Inches(7.5)
    )
    right_bg.fill.solid()
    right_bg.fill.fore_color.rgb = hex_to_rgb(PANEL_NEUTRAL)
    right_bg.line.fill.background()

    # Decorative rounded rectangle 1 (purple)
    r1 = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.6), Inches(1.0), Inches(3.8), Inches(2.2)
    )
    r1.adjustments[0] = 0.1
    r1.fill.solid()
    r1.fill.fore_color.rgb = hex_to_rgb(PANEL_LAVENDER)
    r1.line.color.rgb = hex_to_rgb(COLOR_PURPLE)
    r1.line.width = Pt(1.5)
    add_soft_shadow(r1)

    # Decorative circle (orange)
    c1 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(9.8), Inches(2.6), Inches(1.6), Inches(1.6)
    )
    c1.fill.solid()
    c1.fill.fore_color.rgb = hex_to_rgb(COLOR_ORANGE)
    c1.line.fill.background()
    add_soft_shadow(c1)

    # Decorative rounded rectangle 2 (teal)
    r2 = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.4), Inches(3.8), Inches(4.2), Inches(2.6)
    )
    r2.adjustments[0] = 0.08
    r2.fill.solid()
    r2.fill.fore_color.rgb = hex_to_rgb(PANEL_LIGHT_BLUE)
    r2.line.color.rgb = hex_to_rgb(COLOR_TEAL)
    r2.line.width = Pt(1.2)
    add_soft_shadow(r2)

    # Small gold circle
    c2 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(11.2), Inches(4.8), Inches(0.9), Inches(0.9)
    )
    c2.fill.solid()
    c2.fill.fore_color.rgb = hex_to_rgb(COLOR_GOLD)
    c2.line.fill.background()


def render_problem_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Problem slide: purpose strip (blue pill + first bullet summary in light blue panel), then 2-3 cards with red/orange/gold accents."""
    c_left, c_top, c_width, c_height = bounds
    bullets = slide_model.bullets

    if not bullets:
        return

    first_bullet = bullets[0]
    rem_bullets = bullets[1:] if len(bullets) > 1 else []

    # Purpose strip
    strip_h = Inches(0.9)
    strip = add_card(
        slide,
        c_left,
        c_top,
        c_width,
        strip_h,
        panel_color=PANEL_LIGHT_BLUE,
        accent_color=COLOR_BLUE,
        top_bar=False,
    )
    # Pill 'PROBLEM'
    add_pill(
        slide,
        c_left + Inches(0.25),
        c_top + Inches(0.28),
        Inches(1.2),
        Inches(0.34),
        "PROBLEM",
        bg_color=COLOR_BLUE,
        text_color=COLOR_WHITE,
    )

    # First bullet summary inside purpose strip
    is_ni, summary_text = clean_needs_input_text(first_bullet.text)
    if is_ni or first_bullet.source == Source.NEEDS_INPUT:
        summary_text = f"ADD YOUR OWN: {summary_text}"

    tb = slide.shapes.add_textbox(
        c_left + Inches(1.6), c_top + Inches(0.15), c_width - Inches(1.85), Inches(0.6)
    )
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = summary_text
    p.font.name = FONT_FAMILY
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = hex_to_rgb(COLOR_NAVY)

    # Remaining cards (2-3 cards)
    if not rem_bullets:
        return

    num_cards = min(len(rem_bullets), 4)
    cards_top = c_top + strip_h + Inches(0.25)
    cards_h = c_height - strip_h - Inches(0.25)
    gap = Inches(0.25)
    card_w = int((c_width - (gap * (num_cards - 1))) / num_cards)

    problem_accents = [COLOR_RED, COLOR_ORANGE, COLOR_GOLD, COLOR_PURPLE]
    problem_panels = [PANEL_PEACH, PANEL_LIGHT_BLUE, PANEL_LAVENDER, PANEL_NEUTRAL]

    for i in range(num_cards):
        b = rem_bullets[i]
        card_l = c_left + i * (card_w + gap)
        accent = problem_accents[i % len(problem_accents)]
        panel = problem_panels[i % len(problem_panels)]
        is_ni, b_text = clean_needs_input_text(b.text)

        if is_ni or b.source == Source.NEEDS_INPUT:
            add_card(
                slide,
                card_l,
                cards_top,
                card_w,
                cards_h,
                panel_color=PANEL_NEUTRAL,
                dashed=True,
                top_bar=False,
            )
            add_pill(
                slide,
                card_l + Inches(0.2),
                cards_top + Inches(0.2),
                card_w - Inches(0.4),
                Inches(0.32),
                "ADD YOUR OWN",
                bg_color=COLOR_TEXT_MUTED,
            )
            tb_card = slide.shapes.add_textbox(
                card_l + Inches(0.2),
                cards_top + Inches(0.7),
                card_w - Inches(0.4),
                cards_h - Inches(0.9),
            )
            tf_c = tb_card.text_frame
            tf_c.word_wrap = True
            p = tf_c.paragraphs[0]
            p.text = b_text
            p.font.name = FONT_FAMILY
            p.font.size = CARD_BODY_PT
            p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        else:
            add_card(
                slide,
                card_l,
                cards_top,
                card_w,
                cards_h,
                panel_color=panel,
                accent_color=accent,
                top_bar=True,
            )
            heading, body = split_heading_body(b_text)
            tb_card = slide.shapes.add_textbox(
                card_l + Inches(0.25),
                cards_top + Inches(0.25),
                card_w - Inches(0.5),
                cards_h - Inches(0.5),
            )
            tf_c = tb_card.text_frame
            tf_c.word_wrap = True
            p_head = tf_c.paragraphs[0]
            p_head.text = heading
            p_head.font.name = FONT_FAMILY
            p_head.font.size = CARD_HEADING_PT
            p_head.font.bold = True
            p_head.font.color.rgb = hex_to_rgb(COLOR_NAVY)

            if body:
                p_body = tf_c.add_paragraph()
                p_body.space_before = Pt(8)
                p_body.text = body
                p_body.font.name = FONT_FAMILY
                p_body.font.size = CARD_BODY_PT
                p_body.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)


def render_solution_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Solution slide: purpose strip, then numbered flow of 3-5 cards joined by arrows with distinct accents & matching panels."""
    c_left, c_top, c_width, c_height = bounds
    bullets = slide_model.bullets

    if not bullets:
        return

    # Purpose strip
    strip_h = Inches(0.85)
    add_card(
        slide,
        c_left,
        c_top,
        c_width,
        strip_h,
        panel_color=PANEL_MINT,
        accent_color=COLOR_GREEN,
        top_bar=False,
    )
    add_pill(
        slide,
        c_left + Inches(0.25),
        c_top + Inches(0.25),
        Inches(1.2),
        Inches(0.34),
        "SOLUTION",
        bg_color=COLOR_GREEN,
    )

    first_bullet = bullets[0]
    is_ni, summary_text = clean_needs_input_text(first_bullet.text)
    if is_ni or first_bullet.source == Source.NEEDS_INPUT:
        summary_text = f"ADD YOUR OWN: {summary_text}"

    tb = slide.shapes.add_textbox(
        c_left + Inches(1.6), c_top + Inches(0.12), c_width - Inches(1.85), Inches(0.6)
    )
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = summary_text
    p.font.name = FONT_FAMILY
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = hex_to_rgb(COLOR_NAVY)

    # Numbered flow cards joined by arrows
    flow_bullets = bullets[1:] if len(bullets) > 1 else bullets
    num_cards = min(len(flow_bullets), 5)
    if num_cards == 0:
        return

    cards_top = c_top + strip_h + Inches(0.3)
    cards_h = c_height - strip_h - Inches(0.3)

    # Total width divided between cards and arrows
    arrow_w = Inches(0.28)
    arrow_gap = Inches(0.12)
    total_arrows = num_cards - 1
    total_arrow_space = total_arrows * (arrow_w + 2 * arrow_gap)
    card_w = int((c_width - total_arrow_space) / num_cards)

    cur_x = c_left
    for i in range(num_cards):
        b = flow_bullets[i]
        accent = ACCENTS[i % len(ACCENTS)]
        panel = PANELS[i % len(PANELS)]
        is_ni, b_text = clean_needs_input_text(b.text)

        if is_ni or b.source == Source.NEEDS_INPUT:
            add_card(
                slide,
                cur_x,
                cards_top,
                card_w,
                cards_h,
                panel_color=PANEL_NEUTRAL,
                dashed=True,
                top_bar=False,
            )
            add_number_badge(
                slide,
                cur_x + Inches(0.15),
                cards_top + Inches(0.15),
                Inches(0.32),
                str(i + 1),
                bg_color=COLOR_TEXT_MUTED,
            )
            tb_card = slide.shapes.add_textbox(
                cur_x + Inches(0.15),
                cards_top + Inches(0.6),
                card_w - Inches(0.3),
                cards_h - Inches(0.8),
            )
            tf = tb_card.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = f"ADD YOUR OWN: {b_text}"
            p.font.name = FONT_FAMILY
            p.font.size = CARD_BODY_PT
            p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        else:
            add_card(
                slide,
                cur_x,
                cards_top,
                card_w,
                cards_h,
                panel_color=panel,
                accent_color=accent,
                top_bar=True,
            )
            add_number_badge(
                slide,
                cur_x + Inches(0.15),
                cards_top + Inches(0.15),
                Inches(0.32),
                str(i + 1),
                bg_color=accent,
            )
            heading, body = split_heading_body(b_text)
            tb_card = slide.shapes.add_textbox(
                cur_x + Inches(0.15),
                cards_top + Inches(0.6),
                card_w - Inches(0.3),
                cards_h - Inches(0.7),
            )
            tf = tb_card.text_frame
            tf.word_wrap = True
            p_head = tf.paragraphs[0]
            p_head.text = heading
            p_head.font.name = FONT_FAMILY
            p_head.font.size = CARD_HEADING_PT
            p_head.font.bold = True
            p_head.font.color.rgb = hex_to_rgb(COLOR_NAVY)

            if body:
                p_body = tf.add_paragraph()
                p_body.space_before = Pt(6)
                p_body.text = body
                p_body.font.name = FONT_FAMILY
                p_body.font.size = CARD_BODY_PT
                p_body.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)

        cur_x += card_w
        if i < total_arrows:
            cur_x += arrow_gap
            add_arrow_connector(
                slide,
                cur_x,
                cards_top + int(cards_h / 2) - Inches(0.15),
                arrow_w,
                Inches(0.3),
            )
            cur_x += arrow_w + arrow_gap


def render_features_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Features slide: 2x2 or 3x2 card grid, each with colored top bar and pill label from first 1-2 words."""
    c_left, c_top, c_width, c_height = bounds
    bullets = slide_model.bullets
    n = len(bullets)
    if n == 0:
        return

    cols = 3 if n > 4 else 2
    rows = math.ceil(n / cols)

    gap_x = Inches(0.3)
    gap_y = Inches(0.25)
    card_w = int((c_width - (gap_x * (cols - 1))) / cols)
    card_h = int((c_height - (gap_y * (rows - 1))) / rows)

    for i, b in enumerate(bullets):
        r = i // cols
        c = i % cols
        card_l = c_left + c * (card_w + gap_x)
        card_t = c_top + r * (card_h + gap_y)

        accent = ACCENTS[i % len(ACCENTS)]
        panel = PANELS[i % len(PANELS)]
        is_ni, b_text = clean_needs_input_text(b.text)

        if is_ni or b.source == Source.NEEDS_INPUT:
            add_card(
                slide,
                card_l,
                card_t,
                card_w,
                card_h,
                panel_color=PANEL_NEUTRAL,
                dashed=True,
                top_bar=False,
            )
            add_pill(
                slide,
                card_l + Inches(0.2),
                card_t + Inches(0.15),
                Inches(1.6),
                Inches(0.3),
                "ADD YOUR OWN",
                bg_color=COLOR_TEXT_MUTED,
            )
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.2),
                card_t + Inches(0.55),
                card_w - Inches(0.4),
                card_h - Inches(0.65),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = b_text
            p.font.name = FONT_FAMILY
            p.font.size = CARD_BODY_PT
            p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        else:
            add_card(
                slide,
                card_l,
                card_t,
                card_w,
                card_h,
                panel_color=panel,
                accent_color=accent,
                top_bar=True,
            )
            # Pill from first 1-2 words
            words = b_text.split()
            pill_label = " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else "FEATURE")
            if len(pill_label) > 16:
                pill_label = pill_label[:14] + ".."
            pill_w = max(Inches(1.2), Inches(len(pill_label) * 0.11))
            add_pill(
                slide,
                card_l + Inches(0.2),
                card_t + Inches(0.15),
                min(pill_w, card_w - Inches(0.4)),
                Inches(0.28),
                pill_label,
                bg_color=accent,
            )

            heading, body = split_heading_body(b_text)
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.2),
                card_t + Inches(0.55),
                card_w - Inches(0.4),
                card_h - Inches(0.65),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p_head = tf.paragraphs[0]
            p_head.text = heading
            p_head.font.name = FONT_FAMILY
            p_head.font.size = CARD_HEADING_PT
            p_head.font.bold = True
            p_head.font.color.rgb = hex_to_rgb(COLOR_NAVY)

            if body:
                p_body = tf.add_paragraph()
                p_body.space_before = Pt(4)
                p_body.text = body
                p_body.font.name = FONT_FAMILY
                p_body.font.size = CARD_BODY_PT
                p_body.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)


def render_architecture_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
    diagram_png: Path | None,
) -> None:
    """Architecture slide: LARGE diagram frame (white card, 12.3in x 4.2in max, aspect ratio preserved, centered), pill 'SYSTEM ARCHITECTURE', compact notes row below."""
    c_left, c_top, c_width, c_height = bounds

    # Max frame dimensions: 12.3in x 4.0in
    frame_w = c_width
    frame_h = Inches(4.0)

    # Frame card
    frame = add_card(
        slide,
        c_left,
        c_top,
        frame_w,
        frame_h,
        panel_color=PANEL_WHITE,
        accent_color=COLOR_TEAL,
        top_bar=True,
    )
    add_pill(
        slide,
        c_left + Inches(0.3),
        c_top + Inches(0.15),
        Inches(2.2),
        Inches(0.32),
        "SYSTEM ARCHITECTURE",
        bg_color=COLOR_TEAL,
    )

    # Insert Diagram if present
    if diagram_png and Path(diagram_png).exists():
        try:
            from app.services.render import _get_png_dimensions

            img_w, img_h = _get_png_dimensions(Path(diagram_png))
            if img_w > 0 and img_h > 0:
                inner_w = frame_w - Inches(0.6)
                inner_h = frame_h - Inches(0.7)
                scale = min(inner_w / img_w, inner_h / img_h)
                pic_w = int(img_w * scale)
                pic_h = int(img_h * scale)
                pic_l = c_left + int((frame_w - pic_w) / 2)
                pic_t = c_top + Inches(0.55) + int((inner_h - pic_h) / 2)
                slide.shapes.add_picture(str(diagram_png), pic_l, pic_t, pic_w, pic_h)
        except Exception:
            pass

    # Bullets as up to 3 compact notes in a row under the frame
    bullets = slide_model.bullets[:3]
    if bullets:
        notes_top = c_top + frame_h + Inches(0.2)
        notes_h = c_height - frame_h - Inches(0.2)
        gap = Inches(0.25)
        note_w = int((c_width - (gap * (len(bullets) - 1))) / len(bullets))

        for i, b in enumerate(bullets):
            note_l = c_left + i * (note_w + gap)
            is_ni, b_text = clean_needs_input_text(b.text)
            accent = ACCENTS[i % len(ACCENTS)]

            if is_ni or b.source == Source.NEEDS_INPUT:
                add_card(
                    slide,
                    note_l,
                    notes_top,
                    note_w,
                    notes_h,
                    panel_color=PANEL_NEUTRAL,
                    dashed=True,
                    top_bar=False,
                )
                tb = slide.shapes.add_textbox(
                    note_l + Inches(0.15),
                    notes_top + Inches(0.15),
                    note_w - Inches(0.3),
                    notes_h - Inches(0.3),
                )
                tf = tb.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = f"ADD YOUR OWN: {b_text}"
                p.font.name = FONT_FAMILY
                p.font.size = CARD_BODY_PT
                p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
            else:
                add_card(
                    slide,
                    note_l,
                    notes_top,
                    note_w,
                    notes_h,
                    panel_color=PANEL_LIGHT_BLUE,
                    accent_color=accent,
                    top_bar=True,
                )
                heading, body = split_heading_body(b_text)
                tb = slide.shapes.add_textbox(
                    note_l + Inches(0.15),
                    notes_top + Inches(0.15),
                    note_w - Inches(0.3),
                    notes_h - Inches(0.3),
                )
                tf = tb.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = heading
                p.font.name = FONT_FAMILY
                p.font.size = Pt(11)
                p.font.bold = True
                p.font.color.rgb = hex_to_rgb(COLOR_NAVY)
                if body:
                    p2 = tf.add_paragraph()
                    p2.space_before = Pt(3)
                    p2.text = body
                    p2.font.name = FONT_FAMILY
                    p2.font.size = Pt(10)
                    p2.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)


def render_tech_stack_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Tech stack slide: 3-4 columns of cards titled by category (Frontend, Backend / AI, Data, Infra), category pills, tech items as body text."""
    c_left, c_top, c_width, c_height = bounds

    # Categorize bullets or infer from text
    categories: dict[str, list[str]] = {
        "Frontend": [],
        "Backend / AI": [],
        "Data": [],
        "Infra / Other": [],
    }

    fe_kws = ["frontend", "react", "vue", "svelte", "next", "html", "css", "tailwind", "typescript", "ui", "vite", "web"]
    be_kws = ["backend", "fastapi", "python", "node", "express", "django", "flask", "go", "rust", "gemini", "openai", "ai", "llm", "agent", "langchain", "api"]
    db_kws = ["data", "db", "database", "postgres", "sql", "falkordb", "redis", "mongo", "sqlite", "graph", "store"]

    for b in slide_model.bullets:
        is_ni, b_text = clean_needs_input_text(b.text)
        b_lower = b_text.lower()
        if any(kw in b_lower for kw in fe_kws):
            categories["Frontend"].append(b_text)
        elif any(kw in b_lower for kw in db_kws):
            categories["Data"].append(b_text)
        elif any(kw in b_lower for kw in be_kws):
            categories["Backend / AI"].append(b_text)
        else:
            categories["Infra / Other"].append(b_text)

    # Filter out empty categories if we have enough items, or distribute evenly
    active_cats = [(k, v) for k, v in categories.items() if v]
    if len(active_cats) < 3:
        # Fallback to simple column distribution of bullets
        active_cats = []
        cat_names = ["Frontend", "Backend / AI", "Data & Infra"]
        for i, b in enumerate(slide_model.bullets):
            name = cat_names[i % len(cat_names)]
            match = next((item for item in active_cats if item[0] == name), None)
            if not match:
                active_cats.append((name, [b.text]))
            else:
                match[1].append(b.text)

    num_cols = max(1, len(active_cats))
    gap = Inches(0.25)
    col_w = int((c_width - (gap * (num_cols - 1))) / num_cols)

    cat_accents = [COLOR_BLUE, COLOR_PURPLE, COLOR_TEAL, COLOR_ORANGE]
    cat_panels = [PANEL_LIGHT_BLUE, PANEL_LAVENDER, PANEL_MINT, PANEL_PEACH]

    for i, (cat_name, items) in enumerate(active_cats):
        col_l = c_left + i * (col_w + gap)
        accent = cat_accents[i % len(cat_accents)]
        panel = cat_panels[i % len(cat_panels)]

        add_card(
            slide,
            col_l,
            c_top,
            col_w,
            c_height,
            panel_color=panel,
            accent_color=accent,
            top_bar=True,
        )
        add_pill(
            slide,
            col_l + Inches(0.2),
            c_top + Inches(0.2),
            col_w - Inches(0.4),
            Inches(0.32),
            cat_name,
            bg_color=accent,
        )

        tb = slide.shapes.add_textbox(
            col_l + Inches(0.2),
            c_top + Inches(0.65),
            col_w - Inches(0.4),
            c_height - Inches(0.8),
        )
        tf = tb.text_frame
        tf.word_wrap = True

        for j, item in enumerate(items):
            p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
            if j > 0:
                p.space_before = Pt(8)
            is_ni, clean_item = clean_needs_input_text(item)
            if is_ni:
                p.text = f"• [Add: {clean_item}]"
                p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
            else:
                heading, body = split_heading_body(clean_item)
                p.text = f"• {heading}"
                p.font.bold = True
                p.font.color.rgb = hex_to_rgb(COLOR_NAVY)
                if body:
                    p2 = tf.add_paragraph()
                    p2.space_before = Pt(2)
                    p2.text = f"   {body}"
                    p2.font.size = Pt(10)
                    p2.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)
            p.font.name = FONT_FAMILY
            p.font.size = CARD_BODY_PT


def render_demo_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Demo slide: numbered steps as horizontal cards in a row (max 5), each with number badge."""
    c_left, c_top, c_width, c_height = bounds
    bullets = slide_model.bullets
    n = min(len(bullets), 5)
    if n == 0:
        return

    gap = Inches(0.2)
    card_w = int((c_width - (gap * (n - 1))) / n)

    for i in range(n):
        b = bullets[i]
        card_l = c_left + i * (card_w + gap)
        accent = ACCENTS[i % len(ACCENTS)]
        panel = PANELS[i % len(PANELS)]
        is_ni, b_text = clean_needs_input_text(b.text)

        if is_ni or b.source == Source.NEEDS_INPUT:
            add_card(
                slide,
                card_l,
                c_top,
                card_w,
                c_height,
                panel_color=PANEL_NEUTRAL,
                dashed=True,
                top_bar=False,
            )
            add_number_badge(
                slide,
                card_l + Inches(0.15),
                c_top + Inches(0.15),
                Inches(0.32),
                str(i + 1),
                bg_color=COLOR_TEXT_MUTED,
            )
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.15),
                c_top + Inches(0.6),
                card_w - Inches(0.3),
                c_height - Inches(0.8),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = f"ADD YOUR OWN: {b_text}"
            p.font.name = FONT_FAMILY
            p.font.size = CARD_BODY_PT
            p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        else:
            add_card(
                slide,
                card_l,
                c_top,
                card_w,
                c_height,
                panel_color=panel,
                accent_color=accent,
                top_bar=True,
            )
            add_number_badge(
                slide,
                card_l + Inches(0.15),
                c_top + Inches(0.15),
                Inches(0.32),
                str(i + 1),
                bg_color=accent,
            )
            heading, body = split_heading_body(b_text)
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.15),
                c_top + Inches(0.6),
                card_w - Inches(0.3),
                c_height - Inches(0.7),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p_head = tf.paragraphs[0]
            p_head.text = heading
            p_head.font.name = FONT_FAMILY
            p_head.font.size = CARD_HEADING_PT
            p_head.font.bold = True
            p_head.font.color.rgb = hex_to_rgb(COLOR_NAVY)

            if body:
                p_body = tf.add_paragraph()
                p_body.space_before = Pt(8)
                p_body.text = body
                p_body.font.name = FONT_FAMILY
                p_body.font.size = CARD_BODY_PT
                p_body.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)


def render_impact_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Impact slide: KPI cards with big 32pt numbers when present, 'KEY TAKEAWAY' bottom bar using first bullet."""
    c_left, c_top, c_width, c_height = bounds
    bullets = slide_model.bullets
    if not bullets:
        return

    takeaway_bullet = bullets[0]
    kpi_bullets = bullets[1:] if len(bullets) > 1 else bullets

    # Bottom Takeaway Bar
    bar_h = Inches(0.9)
    bar_top = c_top + c_height - bar_h
    add_card(
        slide,
        c_left,
        bar_top,
        c_width,
        bar_h,
        panel_color=PANEL_PEACH,
        accent_color=COLOR_GOLD,
        top_bar=False,
    )

    # Gold left bar
    gold_l = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, c_left, bar_top, Inches(0.1), bar_h
    )
    gold_l.fill.solid()
    gold_l.fill.fore_color.rgb = hex_to_rgb(COLOR_GOLD)
    gold_l.line.fill.background()

    add_pill(
        slide,
        c_left + Inches(0.25),
        bar_top + Inches(0.28),
        Inches(1.6),
        Inches(0.34),
        "KEY TAKEAWAY",
        bg_color=COLOR_GOLD,
        text_color=COLOR_NAVY,
    )

    is_ni, take_text = clean_needs_input_text(takeaway_bullet.text)
    tb_take = slide.shapes.add_textbox(
        c_left + Inches(2.0), bar_top + Inches(0.15), c_width - Inches(2.2), Inches(0.6)
    )
    tf_t = tb_take.text_frame
    tf_t.word_wrap = True
    p_t = tf_t.paragraphs[0]
    p_t.text = take_text
    p_t.font.name = FONT_FAMILY
    p_t.font.size = Pt(12)
    p_t.font.bold = True
    p_t.font.color.rgb = hex_to_rgb(COLOR_NAVY)

    # KPI cards above takeaway bar
    cards_h = bar_top - c_top - Inches(0.3)
    num_kpis = min(len(kpi_bullets), 4)
    if num_kpis == 0:
        return

    gap = Inches(0.25)
    card_w = int((c_width - (gap * (num_kpis - 1))) / num_kpis)

    # Regex for detecting KPI numbers (e.g. 10x, 99.9%, $50K, 500ms, 2,000+)
    num_pattern = re.compile(r"(\$?\d+(?:,\d+)*(?:\.\d+)?(?:%|x|ms|k|m|b|\+)?)\b", re.IGNORECASE)

    for i in range(num_kpis):
        b = kpi_bullets[i]
        card_l = c_left + i * (card_w + gap)
        accent = ACCENTS[i % len(ACCENTS)]
        panel = PANELS[i % len(PANELS)]
        is_ni, b_text = clean_needs_input_text(b.text)

        if is_ni or b.source == Source.NEEDS_INPUT:
            add_card(
                slide,
                card_l,
                c_top,
                card_w,
                cards_h,
                panel_color=PANEL_NEUTRAL,
                dashed=True,
                top_bar=False,
            )
            add_pill(
                slide,
                card_l + Inches(0.2),
                c_top + Inches(0.2),
                card_w - Inches(0.4),
                Inches(0.3),
                "ADD YOUR OWN",
                bg_color=COLOR_TEXT_MUTED,
            )
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.2),
                c_top + Inches(0.6),
                card_w - Inches(0.4),
                cards_h - Inches(0.7),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = b_text
            p.font.name = FONT_FAMILY
            p.font.size = CARD_BODY_PT
            p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        else:
            add_card(
                slide,
                card_l,
                c_top,
                card_w,
                cards_h,
                panel_color=panel,
                accent_color=accent,
                top_bar=True,
            )

            # Look for big number in bullet text
            match = num_pattern.search(b_text)
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.2),
                c_top + Inches(0.25),
                card_w - Inches(0.4),
                cards_h - Inches(0.4),
            )
            tf = tb.text_frame
            tf.word_wrap = True

            if match:
                kpi_number = match.group(1)
                # Remainder of text without number
                desc_text = b_text.replace(kpi_number, "").strip(" -:,")
                p_num = tf.paragraphs[0]
                p_num.text = kpi_number
                p_num.font.name = FONT_FAMILY
                p_num.font.size = KPI_PT
                p_num.font.bold = True
                p_num.font.color.rgb = hex_to_rgb(accent)

                p_desc = tf.add_paragraph()
                p_desc.space_before = Pt(8)
                p_desc.text = desc_text
                p_desc.font.name = FONT_FAMILY
                p_desc.font.size = CARD_BODY_PT
                p_desc.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)
            else:
                add_pill(
                    slide,
                    card_l + Inches(0.2),
                    c_top + Inches(0.2),
                    Inches(1.2),
                    Inches(0.28),
                    "IMPACT",
                    bg_color=accent,
                )
                heading, body = split_heading_body(b_text)
                tb.top = c_top + Inches(0.55)
                p_head = tf.paragraphs[0]
                p_head.text = heading
                p_head.font.name = FONT_FAMILY
                p_head.font.size = CARD_HEADING_PT
                p_head.font.bold = True
                p_head.font.color.rgb = hex_to_rgb(COLOR_NAVY)

                if body:
                    p_body = tf.add_paragraph()
                    p_body.space_before = Pt(6)
                    p_body.text = body
                    p_body.font.name = FONT_FAMILY
                    p_body.font.size = CARD_BODY_PT
                    p_body.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)


def render_roadmap_slide(
    slide: PptxSlide,
    slide_model: Slide,
    bounds: tuple[int, int, int, int],
) -> None:
    """Roadmap slide: horizontal timeline (line, circle nodes, alternating above/below milestone cards, max 5)."""
    c_left, c_top, c_width, c_height = bounds
    bullets = slide_model.bullets
    n = min(len(bullets), 5)
    if n == 0:
        return

    # Baseline position in center
    mid_y = c_top + int(c_height / 2)
    line_l = c_left + Inches(0.5)
    line_w = c_width - Inches(1.0)

    # Draw horizontal baseline
    timeline_bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, line_l, mid_y - Inches(0.03), line_w, Inches(0.06)
    )
    timeline_bar.fill.solid()
    timeline_bar.fill.fore_color.rgb = hex_to_rgb(COLOR_BLUE)
    timeline_bar.line.fill.background()

    card_h = int((c_height / 2) - Inches(0.5))
    step_w = line_w / n if n > 1 else line_w

    for i in range(n):
        b = bullets[i]
        node_x = line_l + int(i * step_w + (step_w / 2 if n > 1 else line_w / 2))
        accent = ACCENTS[i % len(ACCENTS)]
        is_above = (i % 2 == 0)

        # Circle node on line
        circle_d = Inches(0.24)
        c_node = slide.shapes.add_shape(
            MSO_SHAPE.OVAL,
            node_x - int(circle_d / 2),
            mid_y - int(circle_d / 2),
            circle_d,
            circle_d,
        )
        c_node.fill.solid()
        c_node.fill.fore_color.rgb = hex_to_rgb(accent)
        c_node.line.fill.background()

        # Milestone Card
        card_w = min(Inches(2.5), int(step_w * 0.9))
        card_l = node_x - int(card_w / 2)
        card_t = mid_y - card_h - Inches(0.3) if is_above else mid_y + Inches(0.3)

        is_ni, b_text = clean_needs_input_text(b.text)
        if is_ni or b.source == Source.NEEDS_INPUT:
            add_card(
                slide,
                card_l,
                card_t,
                card_w,
                card_h,
                panel_color=PANEL_NEUTRAL,
                dashed=True,
                top_bar=False,
            )
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.1),
                card_t + Inches(0.1),
                card_w - Inches(0.2),
                card_h - Inches(0.2),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = f"ADD: {b_text}"
            p.font.name = FONT_FAMILY
            p.font.size = Pt(10)
            p.font.color.rgb = hex_to_rgb(COLOR_TEXT_MUTED)
        else:
            add_card(
                slide,
                card_l,
                card_t,
                card_w,
                card_h,
                panel_color=PANEL_LIGHT_BLUE,
                accent_color=accent,
                top_bar=True,
            )
            heading, body = split_heading_body(b_text)
            tb = slide.shapes.add_textbox(
                card_l + Inches(0.12),
                card_t + Inches(0.12),
                card_w - Inches(0.24),
                card_h - Inches(0.24),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p_head = tf.paragraphs[0]
            p_head.text = heading
            p_head.font.name = FONT_FAMILY
            p_head.font.size = Pt(11)
            p_head.font.bold = True
            p_head.font.color.rgb = hex_to_rgb(COLOR_NAVY)

            if body:
                p_body = tf.add_paragraph()
                p_body.space_before = Pt(4)
                p_body.text = body
                p_body.font.name = FONT_FAMILY
                p_body.font.size = Pt(10)
                p_body.font.color.rgb = hex_to_rgb(COLOR_TEXT_DARK)


def render_closing_slide(
    slide: PptxSlide,
    slide_model: Slide,
    project_name: str,
) -> None:
    """Closing slide: same style as title with 'Thank you' and repo name/link if present."""
    # Left 60% navy panel
    left_panel = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, Inches(8.0), Inches(7.5)
    )
    left_panel.fill.solid()
    left_panel.fill.fore_color.rgb = hex_to_rgb(COLOR_NAVY)
    left_panel.line.fill.background()

    # Pill
    add_pill(
        slide,
        Inches(0.8),
        Inches(1.5),
        Inches(1.8),
        Inches(0.32),
        "PROJECT DECK",
        bg_color=COLOR_TEAL,
    )

    # Title in white 40pt bold
    title_text = slide_model.title.text if slide_model.title and slide_model.title.text else "Thank you"
    title_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 0), None)
    if title_ph:
        title_ph.left = Inches(0.8)
        title_ph.top = Inches(2.2)
        title_ph.width = Inches(6.4)
        title_ph.height = Inches(1.5)
        title_ph.text = title_text
        tf = title_ph.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        for p in tf.paragraphs:
            p.font.name = FONT_FAMILY
            p.font.size = Pt(40)
            p.font.bold = True
            p.font.color.rgb = hex_to_rgb(COLOR_WHITE)
    else:
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(2.2), Inches(6.4), Inches(1.5))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.name = FONT_FAMILY
        p.font.size = Pt(40)
        p.font.bold = True
        p.font.color.rgb = hex_to_rgb(COLOR_WHITE)

    # Gold accent line
    gold_line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(3.9), Inches(3.5), Inches(0.06)
    )
    gold_line.fill.solid()
    gold_line.fill.fore_color.rgb = hex_to_rgb(COLOR_GOLD)
    gold_line.line.fill.background()

    # Repo link or project summary
    sub_text = slide_model.subtitle.text if slide_model.subtitle else (project_name or "")
    sub_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 1), None)
    if sub_ph and sub_text:
        sub_ph.left = Inches(0.8)
        sub_ph.top = Inches(4.2)
        sub_ph.width = Inches(6.4)
        sub_ph.height = Inches(1.5)
        sub_ph.text = sub_text
        tf_s = sub_ph.text_frame
        tf_s.word_wrap = True
        tf_s.margin_left = 0
        tf_s.margin_top = 0
        for p in tf_s.paragraphs:
            p.font.name = FONT_FAMILY
            p.font.size = Pt(18)
            p.font.color.rgb = hex_to_rgb(PANEL_LIGHT_BLUE)
    elif sub_text:
        tb_sub = slide.shapes.add_textbox(Inches(0.8), Inches(4.2), Inches(6.4), Inches(1.5))
        tf_sub = tb_sub.text_frame
        tf_sub.word_wrap = True
        p_sub = tf_sub.paragraphs[0]
        p_sub.text = sub_text
        p_sub.font.name = FONT_FAMILY
        p_sub.font.size = Pt(18)
        p_sub.font.color.rgb = hex_to_rgb(PANEL_LIGHT_BLUE)

    # Right 40% decorative shapes
    right_bg = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(8.0), 0, Inches(5.333), Inches(7.5)
    )
    right_bg.fill.solid()
    right_bg.fill.fore_color.rgb = hex_to_rgb(PANEL_NEUTRAL)
    right_bg.line.fill.background()

    r1 = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.8), Inches(1.5), Inches(3.6), Inches(2.5)
    )
    r1.adjustments[0] = 0.08
    r1.fill.solid()
    r1.fill.fore_color.rgb = hex_to_rgb(PANEL_MINT)
    r1.line.color.rgb = hex_to_rgb(COLOR_TEAL)
    r1.line.width = Pt(1.5)
    add_soft_shadow(r1)

    c1 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(9.5), Inches(3.5), Inches(2.0), Inches(2.0)
    )
    c1.fill.solid()
    c1.fill.fore_color.rgb = hex_to_rgb(COLOR_BLUE)
    c1.line.fill.background()
    add_soft_shadow(c1)


# --- MAIN DISPATCHER ---


def is_all_needs_input(slide: Slide) -> bool:
    """Returns True if the slide has bullets and every bullet is a needs_input field."""
    if not slide.bullets:
        return False
    for b in slide.bullets:
        is_ni, _ = clean_needs_input_text(b.text)
        if not (is_ni or b.source == Source.NEEDS_INPUT):
            return False
    return True


def render_designed_slide(
    slide_model: Slide,
    prs: Presentation,
    pptx_slide: PptxSlide,
    diagram_png: Path | None,
    project_name: str,
    slide_num: int,
    total_slides: int,
    custom_body_rect: tuple[int, int, int, int] | None = None,
) -> None:
    """Renders a single slide using modern design recipes with safety net."""
    st = slide_model.type
    is_title = (st == SlideType.TITLE)
    is_closing = (st == SlideType.CLOSING)

    # 1. Standard chrome (if built-in canvas and not title/closing)
    if custom_body_rect is None:
        setup_slide_chrome(
            pptx_slide,
            slide_model.title.text,
            project_name,
            slide_num,
            total_slides,
            is_title_slide=(is_title or is_closing),
        )
        bounds = (CONTENT_LEFT, CONTENT_TOP, CONTENT_WIDTH, CONTENT_HEIGHT)
    else:
        bounds = custom_body_rect

    # 2. Recipe dispatch
    if is_title:
        render_title_slide(pptx_slide, slide_model, project_name)
    elif is_closing:
        render_closing_slide(pptx_slide, slide_model, project_name)
    elif st == SlideType.PROBLEM:
        render_problem_slide(pptx_slide, slide_model, bounds)
    elif st == SlideType.SOLUTION:
        render_solution_slide(pptx_slide, slide_model, bounds)
    elif st == SlideType.FEATURES:
        render_features_slide(pptx_slide, slide_model, bounds)
    elif st == SlideType.ARCHITECTURE:
        render_architecture_slide(pptx_slide, slide_model, bounds, diagram_png)
    elif st == SlideType.TECH_STACK:
        render_tech_stack_slide(pptx_slide, slide_model, bounds)
    elif st == SlideType.DEMO:
        render_demo_slide(pptx_slide, slide_model, bounds)
    elif st == SlideType.IMPACT:
        render_impact_slide(pptx_slide, slide_model, bounds)
    elif st == SlideType.ROADMAP:
        render_roadmap_slide(pptx_slide, slide_model, bounds)
    else:
        # Fallback recipe (generic features grid)
        render_features_slide(pptx_slide, slide_model, bounds)

    # 3. Speaker notes
    if slide_model.notes:
        try:
            pptx_slide.notes_slide.notes_text_frame.text = slide_model.notes
        except Exception:
            pass
