from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlmodel import select

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.storage import (
    rel_to_storage,
    slide_png_path,
    validate_id,
    version_dir,
)
from app.db import session_scope
from app.models.tables import ArtifactRow

router = APIRouter()


def _guard_project_id(pid: str) -> str:
    try:
        return validate_id(pid, "project_id")
    except ValueError as e:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project not found: {e}")


@router.get("/projects/{id}/v/{n}/slides/{k}.png", response_class=FileResponse)
def get_slide_png(id: str, n: int, k: int) -> FileResponse:
    pid = _guard_project_id(id)
    if n <= 0:
        raise ApiError(ErrorCode.NOT_FOUND, f"Invalid version: {n}")
    if k <= 0:
        raise ApiError(ErrorCode.NOT_FOUND, f"Invalid slide index: {k}")

    v_dir = version_dir(pid, n)
    expected_file = slide_png_path(v_dir, k)
    expected_rel = rel_to_storage(expected_file)

    with session_scope() as session:
        stmt = select(ArtifactRow).where(
            ArtifactRow.project_id == pid,
            ArtifactRow.version == n,
            ArtifactRow.kind == "slide_png",
            ArtifactRow.path == expected_rel,
        )
        art = session.exec(stmt).first()
        if art is None:
            raise ApiError(
                ErrorCode.NOT_FOUND,
                f"Slide artifact not found for project {pid} v{n} slide {k}",
            )
        art_path = art.path

    settings = get_settings()
    full_path = (settings.storage_dir / art_path).resolve()
    if not full_path.exists() or not full_path.is_file():
        raise ApiError(ErrorCode.NOT_FOUND, f"Slide file not found on disk: {k}.png")

    return FileResponse(full_path, media_type="image/png")


@router.get("/projects/{id}/v/{n}/deck.pptx", response_class=FileResponse)
def get_deck_pptx(id: str, n: int) -> FileResponse:
    pid = _guard_project_id(id)
    if n <= 0:
        raise ApiError(ErrorCode.NOT_FOUND, f"Invalid version: {n}")

    with session_scope() as session:
        stmt = select(ArtifactRow).where(
            ArtifactRow.project_id == pid,
            ArtifactRow.version == n,
            ArtifactRow.kind == "pptx",
        )
        art = session.exec(stmt).first()
        if art is None:
            raise ApiError(
                ErrorCode.NOT_FOUND,
                f"Deck artifact not found for project {pid} v{n}",
            )
        art_path = art.path

    settings = get_settings()
    full_path = (settings.storage_dir / art_path).resolve()
    if not full_path.exists() or not full_path.is_file():
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"Presentation file not found on disk for project {pid} v{n}",
        )

    return FileResponse(
        full_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"deck_v{n}.pptx",
    )


@router.get("/projects/{id}/v/{n}/diagram.png", response_class=FileResponse)
def get_diagram_png(id: str, n: int) -> FileResponse:
    pid = _guard_project_id(id)
    if n <= 0:
        raise ApiError(ErrorCode.NOT_FOUND, f"Invalid version: {n}")

    with session_scope() as session:
        stmt = select(ArtifactRow).where(
            ArtifactRow.project_id == pid,
            ArtifactRow.version == n,
            ArtifactRow.kind == "diagram",
        )
        art = session.exec(stmt).first()
        if art is None:
            raise ApiError(
                ErrorCode.NOT_FOUND,
                f"Diagram artifact not found for project {pid} v{n}",
            )
        art_path = art.path

    settings = get_settings()
    full_path = (settings.storage_dir / art_path).resolve()
    if not full_path.exists() or not full_path.is_file():
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"Diagram file not found on disk for project {pid} v{n}",
        )

    return FileResponse(full_path, media_type="image/png")
