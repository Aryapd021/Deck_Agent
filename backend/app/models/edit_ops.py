from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field as PydField, field_validator

from app.models.deck import Field, SlideType


class SlidePatch(BaseModel):
    title: Field | None = None
    subtitle: Field | None = None
    summary: Field | None = None
    bullets: list[Field] | None = None
    takeaway: Field | None = None
    notes: str | None = None
    layout_style: str | None = None

    @field_validator("bullets")
    @classmethod
    def _validate_bullets(cls, v: list[Field] | None) -> list[Field] | None:
        if v is not None and len(v) > 5:
            raise ValueError(f"SlidePatch cannot have more than 5 bullets (got {len(v)})")
        return v


class UpdateSlide(BaseModel):
    op: Literal["update_slide"] = "update_slide"
    slide_id: str
    fields: SlidePatch


class MoveSlide(BaseModel):
    op: Literal["move_slide"] = "move_slide"
    slide_id: str
    to_index: int


class DeleteSlide(BaseModel):
    op: Literal["delete_slide"] = "delete_slide"
    slide_id: str


class AddSlide(BaseModel):
    op: Literal["add_slide"] = "add_slide"
    after_slide_id: str
    type: SlideType
    content: SlidePatch


class SetDiagramFocus(BaseModel):
    op: Literal["set_diagram_focus"] = "set_diagram_focus"
    node_ids: list[str]


class Regenerate(BaseModel):
    op: Literal["regenerate"] = "regenerate"
    slide_id: str


EditOp = Annotated[
    Union[
        UpdateSlide,
        MoveSlide,
        DeleteSlide,
        AddSlide,
        SetDiagramFocus,
        Regenerate,
    ],
    PydField(discriminator="op"),
]


class EditPlan(BaseModel):
    ops: list[EditOp]
    message: str | None = None


class ShortenItem(BaseModel):
    slide_id: str
    field: str
    text: str


class ShortenResponse(BaseModel):
    items: list[ShortenItem]
