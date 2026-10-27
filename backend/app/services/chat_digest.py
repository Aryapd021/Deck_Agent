import re

from app.models.facts import ChatDigest
from app.services.llm.base import generate_structured, load_prompt

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 1. PEM private key blocks
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED:PRIVATE_KEY]",
    ),
    # 2. JWTs (eyJ...\.eyJ...\.[\w-]+)
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED:JWT]",
    ),
    # 3. Bearer <16+ token chars>
    (
        re.compile(r"(?i)\bBearer\s+(?!\[REDACTED:)[A-Za-z0-9_\-\.]{16,}\b"),
        "[REDACTED:BEARER_TOKEN]",
    ),
    # 4. GitHub tokens (gh[pousr]_[A-Za-z0-9]{36,} and github_pat_[A-Za-z0-9_]{20,})
    (
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "[REDACTED:GITHUB_TOKEN]",
    ),
    # 5. Slack tokens (xox[abprs]-...)
    (
        re.compile(r"\bxox[abprs]-[0-9A-Za-z_-]{10,}\b"),
        "[REDACTED:SLACK_TOKEN]",
    ),
    # 6. AWS Access Key ID (AKIA[0-9A-Z]{16})
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED:AWS_KEY]",
    ),
    # 7. Google API keys (AIza[0-9A-Za-z_-]{35})
    (
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "[REDACTED:GOOGLE_KEY]",
    ),
    # 8. sk-ant- / sk- style keys (sk-[A-Za-z0-9_-]{20,})
    (
        re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED:API_KEY]",
    ),
    # 9. Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:EMAIL]",
    ),
    # 10. Key/value secrets like (api[_-]?key|secret|token|password|passwd)\s*[:=]\s*\S+ (keep key name, redact value)
    (
        re.compile(r"""(?i)\b([a-z0-9_-]*(?:api[_-]?key|secret|token|password|passwd))(\s*[:=]\s*)(?!\[REDACTED:)[^\s,;'"\r\n]+"""),
        r"\g<1>\g<2>[REDACTED:SECRET]",
    ),
]

MAX_CHARS = 60000
HALF_CHARS = 30000
TRUNCATION_MARKER = "\n[...middle truncated...]\n"


def redact(text: str) -> tuple[str, int]:
    total_count = 0
    current_text = text

    for pattern, repl in PATTERNS:
        current_text, count = pattern.subn(repl, current_text)
        total_count += count

    if len(current_text) > MAX_CHARS:
        current_text = (
            current_text[:HALF_CHARS]
            + TRUNCATION_MARKER
            + current_text[-HALF_CHARS:]
        )

    return current_text, total_count


async def digest(redacted_chat: str) -> ChatDigest:
    if not redacted_chat or not redacted_chat.strip():
        return ChatDigest(
            problem_framing=[],
            decisions=[],
            rejected_options=[],
            insights=[],
        )

    prompt = load_prompt("digest", chat=redacted_chat)
    return await generate_structured(prompt, ChatDigest)
