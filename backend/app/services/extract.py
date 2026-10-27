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
    chat_digest: ChatDigest,
    criteria: str,
) -> ProjectFacts:
    prompt = load_prompt(
        "facts",
        repo_digest=repo_digest.model_dump_json(exclude={"known_paths"}),
        pitch_notes=pitch_notes,
        chat_digest=chat_digest.model_dump_json(),
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
    max_slides = settings.max_slides
    capabilities = manifest_capabilities(manifest)

    prompt = load_prompt(
        "deck",
        facts=facts.model_dump_json(),
        capabilities=json.dumps(capabilities, indent=2),
        max_slides=str(max_slides),
    )
    draft = await generate_structured(prompt, DeckDraft)

    # Determine expected slide types based on max_slides and DROP_ORDER
    expected_types = list(CANONICAL_ORDER)
    for drop_type in DROP_ORDER:
        if len(expected_types) <= max_slides:
            break
        expected_types.remove(drop_type)
    if len(expected_types) > max_slides:
        expected_types = expected_types[:max_slides]

    draft_types = {s.type for s in draft.slides}
    missing_types = [st for st in expected_types if st not in draft_types]
    if missing_types:
        raise ApiError(
            ErrorCode.LLM_INVALID_OUTPUT,
            f"DeckDraft missing required slide types: {[st.value for st in missing_types]}",
            detail={"missing_types": [st.value for st in missing_types]},
        )

    # Dedup duplicate types (keep first) and filter to expected types
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
        schema_version=1,
        project_id=project_id,
        version=version,
        template_id=manifest.template_id,
        slides=final_slides,
        diagram=facts.components,
    )
