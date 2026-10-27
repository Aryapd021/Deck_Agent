from pathlib import Path
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.services.llm import base as llm_base
from app.services.llm.base import (
    LLMProvider,
    generate_structured,
    get_provider,
    load_prompt,
)


class SampleModel(BaseModel):
    name: str
    count: int


class FakeProvider(LLMProvider):
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete_json(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise RuntimeError("FakeProvider ran out of configured responses")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.mark.asyncio
async def test_valid_output_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider(['{"name": "DeckAgent", "count": 10}'])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    res = await generate_structured("Create a deck", SampleModel)
    assert res == SampleModel(name="DeckAgent", count=10)
    assert len(fake.calls) == 1
    assert "Create a deck" in fake.calls[0]
    assert "Return ONLY a JSON object that validates against this schema" in fake.calls[0]


@pytest.mark.asyncio
async def test_invalid_then_valid_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider([
        '{"name": "DeckAgent", "count": "invalid_int"}',
        '{"name": "DeckAgent", "count": 10}',
    ])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    res = await generate_structured("Create a deck", SampleModel)
    assert res == SampleModel(name="DeckAgent", count=10)
    assert len(fake.calls) == 2
    second_prompt = fake.calls[1]
    assert "Your previous output failed validation. Return corrected JSON only." in second_prompt
    assert "invalid_int" in second_prompt or "validation error" in second_prompt.lower()


@pytest.mark.asyncio
async def test_invalid_twice_raises_llm_invalid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider([
        '{"name": "DeckAgent", "count": "invalid_int_1"}',
        '{"name": "DeckAgent", "count": "invalid_int_2"}',
    ])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    with pytest.raises(ApiError) as exc_info:
        await generate_structured("Create a deck", SampleModel)

    assert exc_info.value.code == ErrorCode.LLM_INVALID_OUTPUT
    assert "errors" in exc_info.value.detail
    assert len(exc_info.value.detail["errors"]) > 0
    assert len(fake.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fenced_text",
    [
        '```json\n{"name": "DeckAgent", "count": 10}\n```',
        '```\n{"name": "DeckAgent", "count": 10}\n```',
        '```JSON\n{"name": "DeckAgent", "count": 10}\n```',
        '```json {"name": "DeckAgent", "count": 10} ```',
    ],
)
async def test_fenced_json_accepted(monkeypatch: pytest.MonkeyPatch, fenced_text: str) -> None:
    fake = FakeProvider([fenced_text])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    res = await generate_structured("Create a deck", SampleModel)
    assert res == SampleModel(name="DeckAgent", count=10)


@pytest.mark.asyncio
async def test_provider_sdk_exception_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider([RuntimeError("SDK connection timeout or auth failure")])
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    with pytest.raises(ApiError) as exc_info:
        await generate_structured("Create a deck", SampleModel)

    assert exc_info.value.code == ErrorCode.LLM_INVALID_OUTPUT
    assert exc_info.value.message == "LLM call failed"
    assert exc_info.value.detail == {"kind": "provider_error"}


def test_missing_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.llm.base.get_settings",
        lambda: Settings(llm_provider="gemini", gemini_api_key=""),
    )
    llm_base.get_provider.cache_clear()
    try:
        with pytest.raises(ApiError) as exc_info:
            get_provider()
        assert exc_info.value.code == ErrorCode.TOOL_MISSING
        assert exc_info.value.message == "GEMINI_API_KEY is not set"
    finally:
        llm_base.get_provider.cache_clear()


def test_missing_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.llm.base.get_settings",
        lambda: Settings(llm_provider="anthropic", anthropic_api_key=""),
    )
    llm_base.get_provider.cache_clear()
    try:
        with pytest.raises(ApiError) as exc_info:
            get_provider()
        assert exc_info.value.code == ErrorCode.TOOL_MISSING
        assert exc_info.value.message == "ANTHROPIC_API_KEY is not set"
    finally:
        llm_base.get_provider.cache_clear()


def test_load_prompt_replaces_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p_file = tmp_path / "greeting.md"
    p_file.write_text("Hello {{name}}! Welcome to {{place}}.", encoding="utf-8")
    monkeypatch.setattr(llm_base, "PROMPTS_DIR", tmp_path)

    result = load_prompt("greeting", name="DeckAgent", place="Production")
    assert result == "Hello DeckAgent! Welcome to Production."


def test_load_prompt_leftover_token_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p_file = tmp_path / "broken.md"
    p_file.write_text("Hello {{name}}! Token {{missing}} is here.", encoding="utf-8")
    monkeypatch.setattr(llm_base, "PROMPTS_DIR", tmp_path)

    with pytest.raises(ValueError) as exc_info:
        load_prompt("broken", name="DeckAgent")
    assert "missing" in str(exc_info.value)


def test_load_prompt_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_base, "PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent_prompt")


@pytest.mark.asyncio
async def test_live_check_optional() -> None:
    settings = llm_base.get_settings()
    if not settings.gemini_api_key or settings.llm_provider != "gemini":
        pytest.skip("No GEMINI_API_KEY set or llm_provider is not gemini")

    class ToySchema(BaseModel):
        ping: str

    llm_base.get_provider.cache_clear()
    try:
        res = await generate_structured("Answer with ping='pong'", ToySchema)
        assert isinstance(res, ToySchema)
        assert res.ping.lower() == "pong"
    except ApiError as e:
        pytest.skip(f"Live API check skipped due to provider status: {e.message}")
