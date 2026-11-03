import asyncio
import logging

from google import genai
from google.genai import types

from app.config import get_settings
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)

FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
]


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.client = genai.Client(api_key=self.api_key)

    async def complete_json(self, prompt: str) -> str:
        config = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        )

        candidates = [self.model]
        for fb in FALLBACK_MODELS:
            if fb not in candidates:
                candidates.append(fb)

        last_error = None
        for candidate_model in candidates:
            for attempt in range(2):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=candidate_model,
                        contents=prompt,
                        config=config,
                    )
                    text = response.text or ""
                    if candidate_model != self.model:
                        logger.info("Switched active Gemini model from %s to %s", self.model, candidate_model)
                        self.model = candidate_model
                    return text
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    is_transient = "429" in err_str or "resource_exhausted" in err_str or "503" in err_str
                    if is_transient and attempt < 1:
                        logger.warning(
                            "Gemini model %s transient error (attempt %d/2), retrying in 1.5s: %s",
                            candidate_model,
                            attempt + 1,
                            e,
                        )
                        await asyncio.sleep(1.5)
                        continue
                    # If 404 (model retired) or exhausted transient errors, break to next candidate model
                    logger.warning(
                        "Gemini model %s failed (%s), trying fallback candidate if available",
                        candidate_model,
                        e,
                    )
                    break

        if last_error:
            raise last_error
        raise RuntimeError("All Gemini candidate models failed to complete request")
