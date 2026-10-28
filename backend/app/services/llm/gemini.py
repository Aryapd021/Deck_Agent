import asyncio
import logging

from google import genai
from google.genai import types

from app.config import get_settings
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)


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
        for attempt in range(3):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                return response.text or ""
            except Exception as e:
                err_str = str(e).lower()
                is_transient = "429" in err_str or "resource_exhausted" in err_str or "503" in err_str
                if is_transient and attempt < 2:
                    delay = 2.0 * (attempt + 1)
                    logger.warning(
                        "Gemini rate limit or transient error (attempt %d/3), retrying in %.1fs: %s",
                        attempt + 1,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
