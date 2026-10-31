from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from sqlmodel import Session, func, select

from app.core.errors import ApiError, ErrorCode
from app.core.jobs import (
    JobContext,
    JobResult,
    release,
    run_edit,
    run_generate,
    start_job,
    try_acquire,
    commit_version,
)
from app.core.storage import copy_version_dir, new_temp_version_dir, template_pptx_path
from app.db import get_session
from app.models.api import (
    CreateProjectRequest,
    EditRequest,
    GenerateRequest,
    InputsOut,
    InputsRequest,
    JobAccepted,
    ProjectOut,
    SetTemplateRequest,
    SetThemeRequest,
    TemplateManifest,
    VersionInfo,
)
from app.models.deck import Deck
from app.models.tables import DeckVersionRow, ProjectInputRow, ProjectRow, TemplateRow
from app.services.chat_digest import redact
from app.services.preview import generate_previews
from app.services.render import render_deck
from app.services.template_inspector import set_template, set_theme
import asyncio

router = APIRouter(tags=["projects"])


def _get_default_manifest_json() -> str:
    from app.core.storage import template_manifest_path
    for tid in ("default", "tmpl_1"):
        try:
            p = template_manifest_path(tid)
            if p.exists():
                return p.read_text(encoding="utf-8")
        except Exception:
            pass
    return "{}"


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    req: CreateProjectRequest,
    session: Session = Depends(get_session),
) -> ProjectOut:
    template = session.get(TemplateRow, req.template_id)
    if not template and req.template_id in ("default", "", None):
        default_tmpl = session.get(TemplateRow, "default") or session.exec(select(TemplateRow)).first()
        if default_tmpl:
            template = default_tmpl
            req.template_id = default_tmpl.id
        else:
            default_row = TemplateRow(
                id="default",
                name="Default Template",
                path="templates/default/template.pptx",
                manifest_json=_get_default_manifest_json(),
                created_at=datetime.now(timezone.utc),
            )
            session.add(default_row)
            session.commit()
            session.refresh(default_row)
            template = default_row
            req.template_id = "default"
    elif not template:
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
    req: GenerateRequest | None = None,
    session: Session = Depends(get_session),
) -> JobAccepted:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    if req and req.template_id:
        tmpl = session.get(TemplateRow, req.template_id)
        if not tmpl:
            raise ApiError(ErrorCode.NOT_FOUND, f"Template {req.template_id} not found")
        project.template_id = req.template_id
        session.add(project)
        session.commit()

    await try_acquire(id)
    try:
        job_id = await start_job(id, "generate", run_generate)
    except Exception:
        release(id)
        raise

    return JobAccepted(job_id=job_id)


@router.post("/projects/{id}/edit", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def edit_deck(
    id: str,
    req: EditRequest,
    session: Session = Depends(get_session),
) -> JobAccepted:
    project = session.get(ProjectRow, id)
    if not project and req.deck:
        tmpl_id = getattr(req.deck, "template_id", "default") or "default"
        tmpl = session.get(TemplateRow, tmpl_id) or session.get(TemplateRow, "default")
        if not tmpl:
            tmpl = TemplateRow(
                id=tmpl_id,
                name="Default Template",
                path="templates/default/template.pptx",
                manifest_json=_get_default_manifest_json(),
                created_at=datetime.now(timezone.utc),
            )
            session.add(tmpl)
            session.commit()
            session.refresh(tmpl)
        project = ProjectRow(
            id=id,
            name="Project",
            repo_ref="",
            template_id=tmpl.id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(project)
        session.commit()
        session.refresh(project)

    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    await try_acquire(id)
    try:
        stmt = (
            select(func.max(DeckVersionRow.version))
            .where(DeckVersionRow.project_id == id)
        )
        max_v = session.exec(stmt).first()
        if (max_v is None or max_v == 0) and req.deck:
            v_row = DeckVersionRow(
                project_id=id,
                version=req.base_version,
                created_by="generate",
                summary="Base version",
                deck_json=req.deck.model_dump_json(),
                created_at=datetime.now(timezone.utc),
            )
            session.add(v_row)
            session.commit()
            session.refresh(v_row)
            max_v = req.base_version

            from app.core.storage import version_dir
            b_dir = version_dir(id, req.base_version)
            b_dir.mkdir(parents=True, exist_ok=True)
            (b_dir / "deck.json").write_text(req.deck.model_dump_json(), encoding="utf-8")

        if max_v is None or max_v == 0:
            raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} has no deck versions")

        if req.base_version != max_v:
            raise ApiError(
                ErrorCode.STALE_VERSION,
                f"Base version {req.base_version} does not match current version {max_v}",
                detail={"current_version": max_v, "base_version": req.base_version},
            )

        async def runner(ctx: JobContext) -> JobResult:
            return await run_edit(ctx, req)

        job_id = await start_job(id, "edit", runner)
    except Exception:
        release(id)
        raise

    return JobAccepted(job_id=job_id)


@router.put("/projects/{id}/template", response_model=VersionInfo)
async def update_project_template(
    id: str,
    req: SetTemplateRequest,
    session: Session = Depends(get_session),
) -> VersionInfo:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    new_tmpl = session.get(TemplateRow, req.template_id)
    if not new_tmpl:
        raise ApiError(ErrorCode.NOT_FOUND, f"Template {req.template_id} not found")

    manifest = TemplateManifest.model_validate_json(new_tmpl.manifest_json)
    if req.mode is not None:
        manifest.mode = req.mode
        if manifest.profile:
            manifest.profile.mode = req.mode

    # Acquire project lock
    await try_acquire(id)
    try:
        stmt = (
            select(DeckVersionRow)
            .where(DeckVersionRow.project_id == id)
            .order_by(DeckVersionRow.version.desc())
        )
        latest_row = session.exec(stmt).first()
        if not latest_row:
            raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} has no deck versions")

        current = latest_row.version
        deck = Deck.model_validate_json(latest_row.deck_json)

        # set_template validates slots and keeps all text byte-identical (no enforce_fit, no LLM)
        new_deck = set_template(deck, manifest)
        new_version = current + 1
        new_deck.version = new_version

        project.template_id = req.template_id
        session.add(project)
        session.commit()

        tmp_dir = new_temp_version_dir(id, new_version)
        await asyncio.to_thread(copy_version_dir, id, current, tmp_dir)
        tmpl_path = template_pptx_path(req.template_id)
        out_pptx = tmp_dir / "deck.pptx"
        diagram_png = tmp_dir / "diagram.png"

        await asyncio.to_thread(
            render_deck,
            new_deck,
            manifest,
            tmpl_path,
            out_pptx,
            diagram_png if diagram_png.exists() else None,
        )

        try:
            await asyncio.to_thread(generate_previews, out_pptx, tmp_dir)
        except Exception:
            pass

        return await commit_version(
            project_id=id,
            version=new_version,
            deck=new_deck,
            created_by="manual",
            summary=f"Switched template to {manifest.name or req.template_id}",
            tmp_dir=tmp_dir,
            facts=None,
        )
    finally:
        release(id)


@router.put("/projects/{id}/theme", response_model=VersionInfo)
async def update_project_theme(
    id: str,
    req: SetThemeRequest,
    session: Session = Depends(get_session),
) -> VersionInfo:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    template = session.get(TemplateRow, project.template_id)
    if not template:
        raise ApiError(ErrorCode.NOT_FOUND, f"Template {project.template_id} not found")
    manifest = TemplateManifest.model_validate_json(template.manifest_json)

    await try_acquire(id)
    try:
        stmt = (
            select(DeckVersionRow)
            .where(DeckVersionRow.project_id == id)
            .order_by(DeckVersionRow.version.desc())
        )
        latest_row = session.exec(stmt).first()
        if not latest_row:
            raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} has no deck versions")

        current = latest_row.version
        deck = Deck.model_validate_json(latest_row.deck_json)

        new_deck = set_theme(deck, req.theme, req.variant)
        new_version = current + 1
        new_deck.version = new_version

        tmp_dir = new_temp_version_dir(id, new_version)
        await asyncio.to_thread(copy_version_dir, id, current, tmp_dir)
        tmpl_path = template_pptx_path(project.template_id)
        out_pptx = tmp_dir / "deck.pptx"
        diagram_png = tmp_dir / "diagram.png"

        await asyncio.to_thread(
            render_deck,
            new_deck,
            manifest,
            tmpl_path,
            out_pptx,
            diagram_png if diagram_png.exists() else None,
        )

        try:
            await asyncio.to_thread(generate_previews, out_pptx, tmp_dir)
        except Exception:
            pass

        return await commit_version(
            project_id=id,
            version=new_version,
            deck=new_deck,
            created_by="manual",
            summary=f"Changed theme to {req.theme}",
            tmp_dir=tmp_dir,
            facts=None,
        )
    finally:
        release(id)
