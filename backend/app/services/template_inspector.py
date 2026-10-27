import math
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from app.core.errors import ApiError, ErrorCode
from app.core.storage import template_manifest_path
from app.models.api import FitLimits, LayoutInfo, PlaceholderInfo, SlotMap, TemplateManifest

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

    return TemplateManifest(
        template_id=template_id,
        name=name,
        slide_width=slide_width,
        slide_height=slide_height,
        layouts=layouts_info,
        layout_map=final_layout_map,
        slots=slots,
        limits=limits,
        palette=None,
        warnings=warnings,
    )


def rebuild_manifest(
    manifest: TemplateManifest, layout_map: dict[str, int] | None = None
) -> TemplateManifest:
    target_layout_map = dict(layout_map) if layout_map is not None else dict(manifest.layout_map)
    slots, limits, warnings, final_layout_map = _compute_slots_and_limits(
        manifest.layouts, target_layout_map
    )
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
    return TemplateManifest.model_validate_json(content)
