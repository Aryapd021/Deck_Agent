from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, Field as PydField, field_validator

if TYPE_CHECKING:
    from app.models.facts import ComponentGraph


class Source(str, Enum):
    REPO = "repo"
    VOICE = "voice"
    CHAT = "chat"
    USER = "user"
    NEEDS_INPUT = "needs_input"


class Field(BaseModel):
    text: str
    source: Source
    evidence: str | None = None

    @field_validator("text", mode="before")
    @classmethod
    def _validate_text(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("Field text must be a string")
        v = v.strip()
        if not v:
            raise ValueError("Field text cannot be empty or whitespace only")
        return v


class SlideType(str, Enum):
    TITLE = "title"
    PROBLEM = "problem"
    SOLUTION = "solution"
    FEATURES = "features"
    ARCHITECTURE = "architecture"
    TECH_STACK = "tech_stack"
    DEMO = "demo"
    IMPACT = "impact"
    ROADMAP = "roadmap"
    CLOSING = "closing"


class Slide(BaseModel):
    id: str = PydField(default_factory=lambda: uuid4().hex)
    type: SlideType
    title: Field
    subtitle: Field | None = None
    bullets: list[Field] = PydField(default_factory=list)
    image_ref: str | None = None
    notes: str = ""

    @field_validator("bullets")
    @classmethod
    def _validate_bullets(cls, v: list[Field]) -> list[Field]:
        if len(v) > 5:
            raise ValueError(f"Slide can have at most 5 bullets (got {len(v)})")
        return v


class Deck(BaseModel):
    schema_version: int = 1
    project_id: str
    version: int
    template_id: str
    slides: list[Slide]
    diagram: "ComponentGraph | None" = None


class DeckDraft(BaseModel):
    slides: list[Slide]
