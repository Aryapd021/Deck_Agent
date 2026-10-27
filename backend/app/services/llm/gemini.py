from google import genai
from google.genai import types

from app.config import get_settings
from app.services.llm.base import LLMProvider


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
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return response.text or ""
