from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import select

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.storage import (
    rel_to_storage,
    slide_png_path,
    template_pptx_path,
    validate_id,
    version_dir,
)
from app.db import session_scope
from app.models.deck import Deck
from app.models.tables import ArtifactRow, DeckVersionRow, ProjectRow, TemplateRow
from app.services.render import render_deck
from app.services.template_inspector import load_manifest


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

    settings = get_settings()
    v_dir = settings.storage_dir / "projects" / pid / f"v{n}"
    expected_path = (v_dir / "deck.pptx").resolve()

    # 1. If file exists on disk, serve directly
    if expected_path.is_file():
        return FileResponse(
            expected_path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename=f"deck_v{n}.pptx",
        )

    # 2. Check if ArtifactRow points to an existing file
    with session_scope() as session:
        stmt = select(ArtifactRow).where(
            ArtifactRow.project_id == pid,
            ArtifactRow.version == n,
            ArtifactRow.kind == "pptx",
        )
        art = session.exec(stmt).first()
        if art is not None:
            full_path = (settings.storage_dir / art.path).resolve()
            if full_path.is_file():
                return FileResponse(
                    full_path,
                    media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    filename=f"deck_v{n}.pptx",
                )

    # 3. Check if DeckVersionRow or v{n}/deck.json is available to re-render on the fly
    deck_json = None
    with session_scope() as session:
        v_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == pid,
                DeckVersionRow.version == n,
            )
        ).first()
        if v_row:
            deck_json = v_row.deck_json

    if not deck_json:
        deck_json_path = v_dir / "deck.json"
        if deck_json_path.is_file():
            deck_json = deck_json_path.read_text(encoding="utf-8")

    if deck_json:
        try:
            deck = Deck.model_validate_json(deck_json)
            tmpl_id = getattr(deck, "template_id", "default") or "default"
            try:
                manifest = load_manifest(tmpl_id)
            except Exception:
                manifest = load_manifest("default")
            tmpl_path = template_pptx_path(tmpl_id)
            v_dir.mkdir(parents=True, exist_ok=True)
            render_deck(deck, manifest, tmpl_path, expected_path)

            rel_path = rel_to_storage(expected_path)
            with session_scope() as session:
                existing_art = session.exec(
                    select(ArtifactRow).where(
                        ArtifactRow.project_id == pid,
                        ArtifactRow.version == n,
                        ArtifactRow.kind == "pptx",
                    )
                ).first()
                if not existing_art:
                    session.add(
                        ArtifactRow(
                            project_id=pid,
                            version=n,
                            kind="pptx",
                            path=rel_path,
                        )
                    )

            return FileResponse(
                expected_path,
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                filename=f"deck_v{n}.pptx",
            )
        except Exception:
            pass

    raise ApiError(
        ErrorCode.NOT_FOUND,
        f"Deck artifact not found for project {pid} v{n}",
    )


class ExportDeckRequest(BaseModel):
    deck: Deck | None = None


@router.post("/projects/{id}/v/{n}/export")
def export_deck(
    id: str,
    n: int,
    req: ExportDeckRequest | None = None,
) -> Response:
    pid = _guard_project_id(id)
    if n <= 0:
        raise ApiError(ErrorCode.NOT_FOUND, f"Invalid version: {n}")

    settings = get_settings()
    v_dir = settings.storage_dir / "projects" / pid / f"v{n}"
    expected_path = (v_dir / "deck.pptx").resolve()

    deck: Deck | None = req.deck if req else None

    if deck is None:
        deck_json = None
        with session_scope() as session:
            v_row = session.exec(
                select(DeckVersionRow).where(
                    DeckVersionRow.project_id == pid,
                    DeckVersionRow.version == n,
                )
            ).first()
            if v_row:
                deck_json = v_row.deck_json

        if not deck_json:
            deck_json_path = v_dir / "deck.json"
            if deck_json_path.is_file():
                deck_json = deck_json_path.read_text(encoding="utf-8")

        if deck_json:
            deck = Deck.model_validate_json(deck_json)

    if deck is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"No deck data available to export for project {pid} v{n}",
        )

    v_dir.mkdir(parents=True, exist_ok=True)
    deck_json_file = v_dir / "deck.json"
    if not deck_json_file.exists():
        deck_json_file.write_text(deck.model_dump_json(), encoding="utf-8")

    tmpl_id = getattr(deck, "template_id", "default") or "default"
    try:
        manifest = load_manifest(tmpl_id)
    except Exception:
        manifest = load_manifest("default")

    tmpl_path = template_pptx_path(tmpl_id)
    render_deck(deck, manifest, tmpl_path, expected_path)

    # Reconstitute DB records on this container
    with session_scope() as session:
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
            session.flush()

        proj = session.get(ProjectRow, pid)
        if not proj:
            proj = ProjectRow(
                id=pid,
                name=getattr(deck, "title", "Project") or "Project",
                repo_ref="",
                template_id=tmpl.id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(proj)
            session.flush()

        v_row = session.exec(
            select(DeckVersionRow).where(
                DeckVersionRow.project_id == pid,
                DeckVersionRow.version == n,
            )
        ).first()
        if not v_row:
            v_row = DeckVersionRow(
                project_id=pid,
                version=n,
                created_by="generate",
                summary="Exported version",
                deck_json=deck.model_dump_json(),
                created_at=datetime.now(timezone.utc),
            )
            session.add(v_row)

        art = session.exec(
            select(ArtifactRow).where(
                ArtifactRow.project_id == pid,
                ArtifactRow.version == n,
                ArtifactRow.kind == "pptx",
            )
        ).first()
        if not art:
            session.add(
                ArtifactRow(
                    project_id=pid,
                    version=n,
                    kind="pptx",
                    path=rel_to_storage(expected_path),
                )
            )

    pptx_bytes = expected_path.read_bytes()
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f'attachment; filename="deck_v{n}.pptx"',
        },
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
