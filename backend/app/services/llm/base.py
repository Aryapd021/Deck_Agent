from abc import ABC, abstractmethod
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class LLMProvider(ABC):
    @abstractmethod
    async def complete_json(self, prompt: str) -> str:
        """Returns raw text expected to be JSON."""
        pass


@lru_cache
def get_provider() -> LLMProvider:
    settings = get_settings()
    provider_name = settings.llm_provider.lower()

    if provider_name == "gemini":
        if not settings.gemini_api_key or not settings.gemini_api_key.strip():
            raise ApiError(ErrorCode.TOOL_MISSING, "GEMINI_API_KEY is not set")
        from app.services.llm.gemini import GeminiProvider

        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)

    elif provider_name == "anthropic":
        if not settings.anthropic_api_key or not settings.anthropic_api_key.strip():
            raise ApiError(ErrorCode.TOOL_MISSING, "ANTHROPIC_API_KEY is not set")
        from app.services.llm.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    raise NotImplementedError(f"Unsupported LLM provider: {settings.llm_provider}")


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        match = re.match(r"^```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)\s*```$", cleaned)
        if match:
            return match.group(1).strip()
    return cleaned


async def _call_provider_safe(provider: LLMProvider, prompt: str) -> str:
    try:
        return await provider.complete_json(prompt)
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(
            ErrorCode.LLM_INVALID_OUTPUT,
            "LLM call failed",
            detail={"kind": "provider_error"},
        ) from e


async def generate_structured(prompt: str, schema: type[T]) -> T:
    provider = get_provider()
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    instruction = (
        "Return ONLY a JSON object that validates against this schema. No prose, no markdown fences."
    )
    final_prompt = f"{prompt}\n\nSchema:\n{schema_json}\n\n{instruction}"

    raw = await _call_provider_safe(provider, final_prompt)
    cleaned = _strip_fences(raw)

    try:
        return schema.model_validate_json(cleaned)
    except (ValidationError, ValueError) as err1:
        # Retry once
        err_text = str(err1)[:1500]
        retry_prompt = (
            f"{final_prompt}\n\n"
            f"{err_text}\n\n"
            "Your previous output failed validation. Return corrected JSON only."
        )
        raw_retry = await _call_provider_safe(provider, retry_prompt)
        cleaned_retry = _strip_fences(raw_retry)

        try:
            return schema.model_validate_json(cleaned_retry)
        except (ValidationError, ValueError) as err2:
            errors_detail: list[Any]
            if isinstance(err2, ValidationError):
                errors_detail = err2.errors()
            else:
                errors_detail = [str(err2)]

            raise ApiError(
                ErrorCode.LLM_INVALID_OUTPUT,
                f"Failed to validate LLM output against schema {schema.__name__}",
                detail={"errors": errors_detail},
            ) from err2


def load_prompt(name: str, /, **vars: Any) -> str:
    prompt_path = PROMPTS_DIR / f"{name}.md"
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    content = prompt_path.read_text(encoding="utf-8")
    for key, value in vars.items():
        content = content.replace(f"{{{{{key}}}}}", str(value))

    leftover = re.findall(r"\{\{([^}]+)\}\}", content)
    if leftover:
        raise ValueError(f"Unreplaced tokens in prompt '{name}': {', '.join(leftover)}")

    return content
