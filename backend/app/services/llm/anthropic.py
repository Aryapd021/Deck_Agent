import anthropic

from app.config import get_settings
from app.services.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.anthropic_model
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def complete_json(self, prompt: str) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        text_parts: list[str] = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "".join(text_parts)
