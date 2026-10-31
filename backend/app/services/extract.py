import json
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.models.api import FitLimits, SlotMap, TemplateManifest
from app.models.deck import Deck, DeckDraft, SlideType
from app.models.facts import ChatDigest, ProjectFacts, RepoDigest
from app.services.llm.base import generate_structured, load_prompt

CANONICAL_ORDER: list[SlideType] = [
    SlideType.TITLE,
    SlideType.PROBLEM,
    SlideType.SOLUTION,
    SlideType.FEATURES,
    SlideType.ARCHITECTURE,
    SlideType.TECH_STACK,
    SlideType.DEMO,
    SlideType.IMPACT,
    SlideType.ROADMAP,
    SlideType.CLOSING,
]

DROP_ORDER: list[SlideType] = [
    SlideType.ROADMAP,
    SlideType.IMPACT,
    SlideType.TECH_STACK,
]


def manifest_capabilities(manifest: TemplateManifest) -> dict[str, dict[str, Any]]:
    capabilities: dict[str, dict[str, Any]] = {}
    for st in SlideType:
        key = st.value
        limits = manifest.limits.get(key, FitLimits())
        slots = manifest.slots.get(key, SlotMap())
        capabilities[key] = {
            "title_max": limits.title_max,
            "bullet_max": limits.bullet_max,
            "bullets_max": limits.bullets_max,
            "has_subtitle": slots.subtitle_idx is not None,
            "has_picture_area": slots.picture_idx is not None,
        }
    return capabilities


async def extract_facts(
    repo_digest: RepoDigest,
    pitch_notes: str,
    chat_digest: ChatDigest | None,
    criteria: str,
) -> ProjectFacts:
    prompt = load_prompt(
        "facts",
        repo_digest=repo_digest.model_dump_json(exclude={"known_paths"}),
        pitch_notes=pitch_notes,
        chat_digest=chat_digest.model_dump_json() if chat_digest is not None else "",
        criteria=criteria,
    )
    return await generate_structured(prompt, ProjectFacts)


async def build_deck(
    facts: ProjectFacts,
    manifest: TemplateManifest,
    project_id: str,
    version: int,
) -> Deck:
    settings = get_settings()
    capabilities = manifest_capabilities(manifest)

    mode = getattr(manifest, "mode", "layouts")
    is_fill_mode = bool(
        mode == "fill" and manifest.profile and len(manifest.profile.slots) > 0
    )

    slots_str = ""
    if is_fill_mode and manifest.profile:
        slots_data = [
            {
                "slot_index": s.slot_index,
                "slot_title": s.slot_title,
                "role": getattr(s, "role", "body"),
                "rect": getattr(s, "rect", None),
                "hint": s.hint,
            }
            for s in manifest.profile.slots
        ]
        slots_str = json.dumps(slots_data, indent=2)
        effective_max_slides = len(manifest.profile.slots)
    else:
        effective_max_slides = settings.max_slides

    prompt = load_prompt(
        "deck",
        facts=facts.model_dump_json(),
        capabilities=json.dumps(capabilities, indent=2),
        max_slides=str(effective_max_slides),
        slots=slots_str,
    )
    draft = await generate_structured(prompt, DeckDraft)

    if is_fill_mode and manifest.profile:
        slot_count = len(manifest.profile.slots)
        final_slides = []
        for i, s in enumerate(draft.slides):
            s.id = uuid4().hex
            s.slot_index = i
            t_lower = s.title.text.lower()
            if (
                "approach" in t_lower
                or "architecture" in t_lower
                or "technical" in t_lower
                or s.type == SlideType.ARCHITECTURE
            ):
                s.image_ref = "diagram"
            else:
                s.image_ref = None
            final_slides.append(s)

        if len(final_slides) > slot_count:
            final_slides = final_slides[:slot_count]

        return Deck(
            schema_version=2,
            project_id=project_id,
            version=version,
            template_id=manifest.template_id,
            slides=final_slides,
            diagram=facts.components,
        )

    # Layouts mode: determine expected slide types based on max_slides and DROP_ORDER
    expected_types = list(CANONICAL_ORDER)
    for drop_type in DROP_ORDER:
        if len(expected_types) <= effective_max_slides:
            break
        expected_types.remove(drop_type)
    if len(expected_types) > effective_max_slides:
        expected_types = expected_types[:effective_max_slides]

    draft_types = {s.type for s in draft.slides}
    missing_types = [st for st in expected_types if st not in draft_types]
    if missing_types:
        raise ApiError(
            ErrorCode.LLM_INVALID_OUTPUT,
            f"DeckDraft missing required slide types: {[st.value for st in missing_types]}",
            detail={"missing_types": [st.value for st in missing_types]},
        )

    seen_types = set()
    filtered_slides = []
    for s in draft.slides:
        if s.type in expected_types and s.type not in seen_types:
            seen_types.add(s.type)
            s.id = uuid4().hex
            s.image_ref = "diagram" if s.type == SlideType.ARCHITECTURE else None
            filtered_slides.append(s)

    order_map = {st: i for i, st in enumerate(CANONICAL_ORDER)}
    final_slides = sorted(filtered_slides, key=lambda s: order_map[s.type])

    return Deck(
        schema_version=2,
        project_id=project_id,
        version=version,
        template_id=manifest.template_id,
        slides=final_slides,
        diagram=facts.components,
    )
