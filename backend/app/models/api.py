from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field as PydField, model_validator

from app.core.errors import ErrorCode
from app.models.deck import Deck

GENERATE_STAGES: tuple[str, ...] = (
    "ingest",
    "redact",
    "digest",
    "facts",
    "deck",
    "diagram",
    "render",
    "preview",
    "done",
)

EDIT_STAGES: tuple[str, ...] = (
    "interpret",
    "validate",
    "apply",
    "diagram",
    "render",
    "preview",
    "done",
)


class HealthResponse(BaseModel):
    status: str
    dot: bool
    git: bool
    soffice: bool
    preview_mode: str
    llm_provider: str


class ErrorEnvelope(BaseModel):
    code: ErrorCode
    message: str
    detail: dict[str, Any] | None = None


class JobEvent(BaseModel):
    seq: int
    stage: str
    status: Literal["started", "progress", "done", "error"]
    message: str
    version: int | None = None


class JobAccepted(BaseModel):
    job_id: str


class CreateProjectRequest(BaseModel):
    name: str
    repo_url: str | None = None
    repo_path: str | None = None
    template_id: str

    @model_validator(mode="after")
    def _validate_repo_source(self) -> "CreateProjectRequest":
        has_url = bool(self.repo_url and self.repo_url.strip())
        has_path = bool(self.repo_path and self.repo_path.strip())
        if has_url == has_path:
            raise ValueError("Exactly one of repo_url or repo_path must be provided")
        return self


class ProjectOut(BaseModel):
    id: str
    name: str
    repo_ref: str
    template_id: str
    created_at: datetime | str


class InputsRequest(BaseModel):
    pitch_notes: str = ""
    chat_text: str = ""
    criteria: str = ""


class InputsOut(BaseModel):
    pitch_notes: str
    has_chat: bool
    redactions: int
    criteria: str


class EditRequest(BaseModel):
    command: str
    base_version: int
    target_slide_id: str | None = None


class DeckPatchRequest(BaseModel):
    base_version: int
    slide_id: str
    target: Literal["title", "subtitle", "bullet", "layout_style"]
    bullet_index: int | None = None
    text: str


class DeckResponse(BaseModel):
    version: int
    created_by: Literal["generate", "edit", "undo", "manual"]
    summary: str
    deck: Deck


class VersionInfo(BaseModel):
    version: int
    created_by: Literal["generate", "edit", "undo", "manual"]
    summary: str
    created_at: datetime | str


class PlaceholderInfo(BaseModel):
    idx: int
    type: int
    name: str
    left: int
    top: int
    width: int
    height: int
    char_limit: int


class LayoutInfo(BaseModel):
    index: int
    name: str
    placeholders: list[PlaceholderInfo]


class SlotMap(BaseModel):
    title_idx: int | None = None
    subtitle_idx: int | None = None
    body_idx: int | None = None
    picture_idx: int | None = None


class FitLimits(BaseModel):
    title_max: int = 70
    bullet_max: int = 100
    bullets_max: int = 5
    notes_max: int = 600


class TemplateSlot(BaseModel):
    slot_index: int
    slot_title: str
    hint: str = ""


class SlideAnalysisReport(BaseModel):
    slide_index: int
    static_count: int
    fillable_count: int
    free_box: tuple[int, int, int, int] | None = None


class TemplateProfile(BaseModel):
    slide_width: int
    slide_height: int
    scale: float = 1.0
    aspect_ratio: str = "16:9"
    dk1: str = "000000"
    lt1: str = "FFFFFF"
    dk2: str = "1F2937"
    lt2: str = "F6F7F9"
    accent1: str = "006FC0"
    accent2: str = "6B4FA0"
    accent3: str = "1A9C8F"
    accent4: str = "F28C28"
    accent5: str = "2E9E5B"
    accent6: str = "D9A441"
    major_font: str = "Calibri"
    minor_font: str = "Calibri"
    background: Literal["dark", "light"] = "light"
    background_luminance: Literal["dark", "light"] = "light"
    theme_name: str = ""
    role_primary: str = "006FC0"
    role_secondary: str = "6B4FA0"
    role_tertiary: str = "1A9C8F"
    role_quaternary: str = "F28C28"
    role_neutral: str = "6B7280"
    primary: str = "006FC0"
    secondary: str = "6B4FA0"
    tertiary: str = "1A9C8F"
    quaternary: str = "F28C28"
    neutral: str = "6B7280"
    accents: list[str] = PydField(
        default_factory=lambda: ["006FC0", "6B4FA0", "1A9C8F", "F28C28", "2E9E5B", "D9A441"]
    )
    panels: list[str] = PydField(
        default_factory=lambda: ["ECF3FB", "F5F4FA", "E8F5EE", "FDF1E3", "F6F7F9"]
    )
    corner_radius_ratio: float = 0.025
    padding_ratio: float = 0.015
    gap_ratio: float = 0.018
    base_body_size_pt: float = 12.0
    min_body_size_pt: float = 9.0
    mode: Literal["fill", "layouts"] = "layouts"
    slots: list[TemplateSlot] = PydField(default_factory=list)
    slide_reports: list[SlideAnalysisReport] = PydField(default_factory=list)
    analysis_reports: list[SlideAnalysisReport] = PydField(default_factory=list)


class TemplateManifest(BaseModel):
    template_id: str
    name: str
    slide_width: int
    slide_height: int
    layouts: list[LayoutInfo]
    layout_map: dict[str, int]
    slots: dict[str, SlotMap]
    limits: dict[str, FitLimits]
    palette: dict[str, str] | None = None
    warnings: list[str] = PydField(default_factory=list)
    mode: Literal["fill", "layouts"] = "layouts"
    profile: TemplateProfile | None = None


class TemplateOut(BaseModel):
    id: str
    name: str
    manifest: TemplateManifest


class LayoutMapUpdate(BaseModel):
    layout_map: dict[str, int]


class TemplateModeUpdate(BaseModel):
    mode: Literal["fill", "layouts"]


class SetTemplateRequest(BaseModel):
    template_id: str
    mode: Literal["fill", "layouts"] | None = None


class SetThemeRequest(BaseModel):
    theme: str
    variant: str | None = None
