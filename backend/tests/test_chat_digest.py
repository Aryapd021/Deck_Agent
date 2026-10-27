import pytest

from app.models.facts import ChatDigest
from app.services import chat_digest
from app.services.chat_digest import digest, redact
from app.services.llm import base as llm_base
from app.services.llm.base import LLMProvider


class FakeProvider(LLMProvider):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    async def complete_json(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


def test_redact_all_patterns() -> None:
    text_pieces = [
        "sk-ant-api03-12345678901234567890",
        "sk-123456789012345678901234",
        "AIzaSyD-123456789012345678901234567890A",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_123456789012345678901234567890123456",
        "github_pat_1234567890123456789012",
        "xoxb-1234567890-123456789012-abcdef",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J_wStP_GIlWutflmP_wStP_GIlWutflmP",
        "Bearer abcdef12345678901234567890",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
        "developer@example.com",
        "api_key = my_secret_token",
        "password: my_password",
        "token=secret_token",
        "passwd: pass123",
        "secret = shhh",
    ]

    joined = "\n".join(text_pieces)
    redacted_text, count = redact(joined)

    assert count == len(text_pieces)
    assert "[REDACTED:API_KEY]" in redacted_text
    assert "[REDACTED:GOOGLE_KEY]" in redacted_text
    assert "[REDACTED:AWS_KEY]" in redacted_text
    assert "[REDACTED:GITHUB_TOKEN]" in redacted_text
    assert "[REDACTED:SLACK_TOKEN]" in redacted_text
    assert "[REDACTED:JWT]" in redacted_text
    assert "[REDACTED:BEARER_TOKEN]" in redacted_text
    assert "[REDACTED:PRIVATE_KEY]" in redacted_text
    assert "[REDACTED:EMAIL]" in redacted_text
    assert "api_key = [REDACTED:SECRET]" in redacted_text
    assert "password: [REDACTED:SECRET]" in redacted_text
    assert "token=[REDACTED:SECRET]" in redacted_text
    assert "passwd: [REDACTED:SECRET]" in redacted_text
    assert "secret = [REDACTED:SECRET]" in redacted_text

    # None of the raw secrets should remain
    for piece in text_pieces:
        if "=" in piece:
            val = piece.split("=", 1)[1].strip()
            assert val not in redacted_text
        elif ":" in piece and not piece.startswith("Bearer"):
            val = piece.split(":", 1)[1].strip()
            assert val not in redacted_text
        elif not piece.startswith("-----"):
            assert piece not in redacted_text


def test_ordinary_text_and_code_untouched() -> None:
    clean_sample = (
        "def calculate_total(subtotal: float, tax_rate: float) -> float:\n"
        "    return subtotal * (1 + tax_rate)\n\n"
        "We chose SQLite because it requires no external setup and supports atomic transactions.\n"
        "Visit https://fastapi.tiangolo.com for more documentation."
    )
    result, count = redact(clean_sample)
    assert count == 0
    assert result == clean_sample


def test_truncation_over_60000_chars() -> None:
    head = "A" * 35000
    tail = "B" * 35000
    middle_secret = "contact@test.org"
    long_text = f"{head}\n{middle_secret}\n{tail}"

    result, count = redact(long_text)
    assert count == 1
    assert "[...middle truncated...]" in result
    assert result.startswith("A" * 30000)
    assert result.endswith("B" * 30000)


@pytest.mark.asyncio
async def test_empty_chat_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider("should not be called")
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    d1 = await digest("")
    assert d1 == ChatDigest(problem_framing=[], decisions=[], rejected_options=[], insights=[])
    assert len(fake.calls) == 0

    d2 = await digest("   \n\t  ")
    assert d2 == ChatDigest(problem_framing=[], decisions=[], rejected_options=[], insights=[])
    assert len(fake.calls) == 0


@pytest.mark.asyncio
async def test_digest_with_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_json = """{
      "problem_framing": ["Creating decks manually is slow"],
      "decisions": ["Used python-pptx for layout engine"],
      "rejected_options": ["Discarded browser automation due to flakiness"],
      "insights": ["Schema validation prevents hallucination"]
    }"""
    fake = FakeProvider(fake_json)
    monkeypatch.setattr(llm_base, "get_provider", lambda: fake)

    res = await digest("We discussed building DeckAgent to automate pitch decks.")
    assert isinstance(res, ChatDigest)
    assert res.problem_framing == ["Creating decks manually is slow"]
    assert res.decisions == ["Used python-pptx for layout engine"]
    assert res.rejected_options == ["Discarded browser automation due to flakiness"]
    assert res.insights == ["Schema validation prevents hallucination"]

    assert len(fake.calls) == 1
    assert "<chat_transcript>" in fake.calls[0]
    assert "We discussed building DeckAgent to automate pitch decks." in fake.calls[0]
