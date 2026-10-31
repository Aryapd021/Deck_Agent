import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlmodel import Session, select

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.storage import template_pptx_path
from app.db import get_session
from app.models.api import LayoutMapUpdate, TemplateManifest, TemplateModeUpdate, TemplateOut
from app.models.deck import SlideType
from app.models.tables import TemplateRow
from app.services.template_inspector import (
    inspect_template,
    load_manifest,
    rebuild_manifest,
    save_manifest,
)

router = APIRouter(tags=["templates"])


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def upload_template(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> TemplateOut:
    filename = file.filename or "template.pptx"
    if not filename.lower().endswith(".pptx"):
        raise ApiError(ErrorCode.TEMPLATE_INVALID, "Uploaded file must be a .pptx file")

    template_name = name.strip() if name and name.strip() else Path(filename).stem
    template_id = uuid4().hex
    pptx_path = template_pptx_path(template_id)
    pptx_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        content = await file.read()
        pptx_path.write_bytes(content)
    except Exception as e:
        if pptx_path.exists():
            pptx_path.unlink()
        raise ApiError(ErrorCode.TEMPLATE_INVALID, f"Failed to save uploaded file: {e}") from e

    try:
        manifest = await asyncio.to_thread(inspect_template, pptx_path, template_id, template_name)
    except ApiError:
        if pptx_path.exists():
            pptx_path.unlink()
        raise
    except Exception as e:
        if pptx_path.exists():
            pptx_path.unlink()
        raise ApiError(ErrorCode.TEMPLATE_INVALID, f"Failed to inspect template: {e}") from e

    try:
        save_manifest(manifest)
    except Exception as e:
        if pptx_path.exists():
            pptx_path.unlink()
        raise ApiError(ErrorCode.TEMPLATE_INVALID, f"Failed to save template manifest: {e}") from e

    settings = get_settings()
    try:
        rel_path = pptx_path.relative_to(settings.repo_root).as_posix()
    except ValueError:
        rel_path = f"templates/{template_id}.pptx"
    manifest_json = manifest.model_dump_json()

    template_row = TemplateRow(
        id=template_id,
        name=template_name,
        path=rel_path,
        manifest_json=manifest_json,
    )
    session.add(template_row)
    session.commit()
    session.refresh(template_row)

    return TemplateOut(id=template_id, name=template_name, manifest=manifest)


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(session: Session = Depends(get_session)) -> list[TemplateOut]:
    statement = select(TemplateRow).order_by(TemplateRow.created_at.desc())
    rows = session.exec(statement).all()
    result: list[TemplateOut] = []
    for row in rows:
        manifest = TemplateManifest.model_validate_json(row.manifest_json)
        result.append(TemplateOut(id=row.id, name=row.name, manifest=manifest))
    return result


@router.put("/templates/{id}/layout-map", response_model=TemplateOut)
async def update_layout_map(
    id: str,
    update: LayoutMapUpdate,
    session: Session = Depends(get_session),
) -> TemplateOut:
    template_row = session.get(TemplateRow, id)
    if not template_row:
        raise ApiError(ErrorCode.NOT_FOUND, f"Template not found: {id}")

    try:
        manifest = TemplateManifest.model_validate_json(template_row.manifest_json)
    except Exception:
        manifest = load_manifest(id)

    valid_slide_types = {st.value for st in SlideType}
    invalid_keys = [k for k in update.layout_map.keys() if k not in valid_slide_types]

    valid_layout_indices = {l.index for l in manifest.layouts}
    invalid_indices = {
        k: idx for k, idx in update.layout_map.items() if idx not in valid_layout_indices
    }

    if invalid_keys or invalid_indices:
        detail = {}
        if invalid_keys:
            detail["invalid_keys"] = invalid_keys
        if invalid_indices:
            detail["invalid_indices"] = invalid_indices
        raise ApiError(
            ErrorCode.TEMPLATE_INVALID,
            "Invalid layout map: contains unknown slide types or layout indices",
            detail=detail,
        )

    rebuilt = await asyncio.to_thread(rebuild_manifest, manifest, update.layout_map)
    save_manifest(rebuilt)

    template_row.manifest_json = rebuilt.model_dump_json()
    session.add(template_row)
    session.commit()
    session.refresh(template_row)

    return TemplateOut(id=template_row.id, name=template_row.name, manifest=rebuilt)


@router.put("/templates/{id}/mode", response_model=TemplateOut)
async def update_template_mode(
    id: str,
    update: TemplateModeUpdate,
    session: Session = Depends(get_session),
) -> TemplateOut:
    template_row = session.get(TemplateRow, id)
    if not template_row:
        raise ApiError(ErrorCode.NOT_FOUND, f"Template not found: {id}")

    try:
        manifest = TemplateManifest.model_validate_json(template_row.manifest_json)
    except Exception:
        manifest = load_manifest(id)

    if manifest.profile:
        manifest.profile.mode = update.mode
    save_manifest(manifest)

    template_row.manifest_json = manifest.model_dump_json()
    session.add(template_row)
    session.commit()
    session.refresh(template_row)

    return TemplateOut(id=template_row.id, name=template_row.name, manifest=manifest)

