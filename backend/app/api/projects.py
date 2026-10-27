from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.errors import ApiError, ErrorCode
from app.core.jobs import release, run_generate, start_job, try_acquire
from app.db import get_session
from app.models.api import (
    CreateProjectRequest,
    InputsOut,
    InputsRequest,
    JobAccepted,
    ProjectOut,
)
from app.models.tables import ProjectInputRow, ProjectRow, TemplateRow
from app.services.chat_digest import redact

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    req: CreateProjectRequest,
    session: Session = Depends(get_session),
) -> ProjectOut:
    template = session.get(TemplateRow, req.template_id)
    if not template:
        raise ApiError(ErrorCode.NOT_FOUND, f"Template {req.template_id} not found")

    repo_ref = (req.repo_url or req.repo_path or "").strip()
    project_id = uuid4().hex
    now = datetime.now(timezone.utc)

    project = ProjectRow(
        id=project_id,
        name=req.name,
        repo_ref=repo_ref,
        template_id=req.template_id,
        created_at=now,
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    return ProjectOut(
        id=project.id,
        name=project.name,
        repo_ref=project.repo_ref,
        template_id=project.template_id,
        created_at=project.created_at,
    )


@router.put("/projects/{id}/inputs", response_model=InputsOut)
async def update_inputs(
    id: str,
    req: InputsRequest,
    session: Session = Depends(get_session),
) -> InputsOut:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    redacted_chat, count = redact(req.chat_text)
    now = datetime.now(timezone.utc)

    input_row = session.get(ProjectInputRow, id)
    if input_row:
        input_row.pitch_notes = req.pitch_notes
        input_row.chat_redacted = redacted_chat
        input_row.criteria = req.criteria
        input_row.updated_at = now
        session.add(input_row)
    else:
        input_row = ProjectInputRow(
            project_id=id,
            pitch_notes=req.pitch_notes,
            chat_redacted=redacted_chat,
            criteria=req.criteria,
            updated_at=now,
        )
        session.add(input_row)

    session.commit()

    return InputsOut(
        pitch_notes=req.pitch_notes,
        has_chat=bool(req.chat_text and req.chat_text.strip()),
        redactions=count,
        criteria=req.criteria,
    )


@router.post("/projects/{id}/generate", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def generate_deck(
    id: str,
    session: Session = Depends(get_session),
) -> JobAccepted:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    await try_acquire(id)
    try:
        job_id = await start_job(id, "generate", run_generate)
    except Exception:
        release(id)
        raise

    return JobAccepted(job_id=job_id)
