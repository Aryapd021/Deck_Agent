from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TemplateRow(SQLModel, table=True):
    __tablename__ = "template"

    id: str = Field(primary_key=True)
    name: str
    path: str
    manifest_json: str
    created_at: datetime = Field(default_factory=utc_now)


class ProjectRow(SQLModel, table=True):
    __tablename__ = "project"

    id: str = Field(primary_key=True)
    name: str
    repo_ref: str
    template_id: str = Field(foreign_key="template.id")
    created_at: datetime = Field(default_factory=utc_now)


class ProjectInputRow(SQLModel, table=True):
    __tablename__ = "project_input"

    project_id: str = Field(primary_key=True, foreign_key="project.id")
    pitch_notes: str = Field(default="")
    chat_redacted: str = Field(default="")
    criteria: str = Field(default="")
    updated_at: datetime = Field(default_factory=utc_now)


class DeckVersionRow(SQLModel, table=True):
    __tablename__ = "deck_version"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_deck_version_project_version"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    version: int
    deck_json: str
    created_by: str
    summary: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)


class JobRow(SQLModel, table=True):
    __tablename__ = "job"

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    type: str
    status: str = Field(default="running")
    stage: Optional[str] = Field(default=None)
    error_code: Optional[str] = Field(default=None)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: Optional[datetime] = Field(default=None)


class JobEventRow(SQLModel, table=True):
    __tablename__ = "job_event"
    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_job_event_job_seq"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True, foreign_key="job.id")
    seq: int
    stage: str
    status: str
    message: str
    ts: datetime = Field(default_factory=utc_now)


class ArtifactRow(SQLModel, table=True):
    __tablename__ = "artifact"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    version: int
    kind: str
    path: str
