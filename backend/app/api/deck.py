import asyncio
import shutil

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.errors import ApiError, ErrorCode
from app.core.jobs import commit_version, release, try_acquire
from app.core.storage import copy_version_dir, new_temp_version_dir, template_pptx_path
from app.db import get_session
from app.models.api import DeckPatchRequest, DeckResponse, TemplateManifest, VersionInfo
from app.models.deck import Deck, Field, Source
from app.models.tables import DeckVersionRow, ProjectRow, TemplateRow
from app.services.preview import generate_previews
from app.services.render import render_deck
from app.services.validate import enforce_fit

router = APIRouter(tags=["deck"])


@router.get("/projects/{id}/deck", response_model=DeckResponse)
async def get_latest_deck(
    id: str,
    session: Session = Depends(get_session),
) -> DeckResponse:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    stmt = (
        select(DeckVersionRow)
        .where(DeckVersionRow.project_id == id)
        .order_by(DeckVersionRow.version.desc())
    )
    latest_version = session.exec(stmt).first()
    if not latest_version:
        raise ApiError(ErrorCode.NOT_FOUND, f"No deck version found for project {id}")

    deck = Deck.model_validate_json(latest_version.deck_json)

    return DeckResponse(
        version=latest_version.version,
        created_by=latest_version.created_by,  # type: ignore
        summary=latest_version.summary or "",
        deck=deck,
    )


@router.get("/projects/{id}/versions", response_model=list[VersionInfo])
async def list_versions(
    id: str,
    session: Session = Depends(get_session),
) -> list[VersionInfo]:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    stmt = (
        select(DeckVersionRow)
        .where(DeckVersionRow.project_id == id)
        .order_by(DeckVersionRow.version.desc())
    )
    rows = session.exec(stmt).all()

    return [
        VersionInfo(
            version=row.version,
            created_by=row.created_by,  # type: ignore
            summary=row.summary or "",
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/projects/{id}/undo", response_model=DeckResponse)
async def undo_deck(
    id: str,
    session: Session = Depends(get_session),
) -> DeckResponse:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    await try_acquire(id)
    try:
        stmt = (
            select(DeckVersionRow)
            .where(DeckVersionRow.project_id == id)
            .order_by(DeckVersionRow.version.desc())
        )
        latest_row = session.exec(stmt).first()
        current = latest_row.version if latest_row else 0
        if current < 2:
            raise ApiError(ErrorCode.NOT_FOUND, "Nothing to undo")

        source_v = current - 1
        stmt_source = select(DeckVersionRow).where(
            DeckVersionRow.project_id == id,
            DeckVersionRow.version == source_v,
        )
        source_row = session.exec(stmt_source).first()
        if not source_row:
            raise ApiError(ErrorCode.NOT_FOUND, f"Source version {source_v} not found")

        source_deck = Deck.model_validate_json(source_row.deck_json)
        new_version = current + 1
        tmp_dir = new_temp_version_dir(id, new_version)

        try:
            await asyncio.to_thread(copy_version_dir, id, source_v, tmp_dir)
            new_deck = source_deck.model_copy(deep=True)
            new_deck.version = new_version
            summary = f"Undo: restored v{source_v}"

            await commit_version(
                project_id=id,
                version=new_version,
                deck=new_deck,
                created_by="undo",
                summary=summary,
                tmp_dir=tmp_dir,
                facts=None,
            )
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        return DeckResponse(
            version=new_version,
            created_by="undo",
            summary=summary,
            deck=new_deck,
        )
    finally:
        release(id)


@router.patch("/projects/{id}/deck", response_model=DeckResponse)
async def patch_deck(
    id: str,
    req: DeckPatchRequest,
    session: Session = Depends(get_session),
) -> DeckResponse:
    project = session.get(ProjectRow, id)
    if not project:
        raise ApiError(ErrorCode.NOT_FOUND, f"Project {id} not found")

    await try_acquire(id)
    try:
        stmt = (
            select(DeckVersionRow)
            .where(DeckVersionRow.project_id == id)
            .order_by(DeckVersionRow.version.desc())
        )
        latest_row = session.exec(stmt).first()
        current = latest_row.version if latest_row else 0
        if not latest_row or req.base_version != current:
            raise ApiError(
                ErrorCode.STALE_VERSION,
                f"Base version {req.base_version} does not match current version {current}",
                {"current_version": current, "base_version": req.base_version},
            )

        template = session.get(TemplateRow, project.template_id)
        if not template:
            raise ApiError(ErrorCode.NOT_FOUND, f"Template {project.template_id} not found")
        manifest = TemplateManifest.model_validate_json(template.manifest_json)

        deck = Deck.model_validate_json(latest_row.deck_json)
        new_deck = deck.model_copy(deep=True)

        slide_entry = next(((i, s) for i, s in enumerate(new_deck.slides) if s.id == req.slide_id), None)
        if slide_entry is None:
            raise ApiError(ErrorCode.NOT_FOUND, f"Slide {req.slide_id} not found")
        slide_idx, slide = slide_entry
        n = slide_idx + 1

        stripped_text = req.text.strip()
        if not stripped_text:
            raise ApiError(ErrorCode.NOT_FOUND, "Text cannot be empty")

        if req.target == "title":
            slide.title.text = stripped_text
            slide.title.source = Source.USER
            slide.title.evidence = None
            target_desc = "title"
        elif req.target == "subtitle":
            if slide.subtitle is None:
                slide.subtitle = Field(text=stripped_text, source=Source.USER, evidence=None)
            else:
                slide.subtitle.text = stripped_text
                slide.subtitle.source = Source.USER
                slide.subtitle.evidence = None
            target_desc = "subtitle"
        elif req.target == "bullet":
            if (
                req.bullet_index is None
                or req.bullet_index < 0
                or req.bullet_index >= len(slide.bullets)
            ):
                raise ApiError(
                    ErrorCode.NOT_FOUND,
                    f"Bullet index {req.bullet_index} not found on slide {req.slide_id}",
                )
            bullet = slide.bullets[req.bullet_index]
            bullet.text = stripped_text
            bullet.source = Source.USER
            bullet.evidence = None
            target_desc = f"bullet {req.bullet_index}"
        elif req.target == "layout_style":
            style_val = stripped_text.lower()
            if style_val not in ("vertical", "horizontal", "auto", "grid"):
                raise ApiError(ErrorCode.BAD_REQUEST, f"Invalid layout style: {stripped_text}")
            slide.layout_style = style_val
            target_desc = f"layout to {style_val}"
        else:
            raise ApiError(ErrorCode.NOT_FOUND, f"Unknown target {req.target}")

        new_deck, warnings = await enforce_fit(new_deck, manifest, use_llm=False)

        new_version = current + 1
        new_deck.version = new_version

        summary = f"Edited slide {n} {target_desc}"
        if warnings:
            summary = f"{summary} ({len(warnings)} fields truncated)"

        tmp_dir = new_temp_version_dir(id, new_version)
        try:
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

            slides_dir = tmp_dir / "slides"
            if slides_dir.exists():
                for f in slides_dir.glob("*.png"):
                    try:
                        f.unlink()
                    except Exception:
                        pass

            try:
                await asyncio.to_thread(generate_previews, out_pptx, tmp_dir)
            except Exception:
                pass

            await commit_version(
                project_id=id,
                version=new_version,
                deck=new_deck,
                created_by="manual",
                summary=summary,
                tmp_dir=tmp_dir,
                facts=None,
            )
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        return DeckResponse(
            version=new_version,
            created_by="manual",
            summary=summary,
            deck=new_deck,
        )
    finally:
        release(id)


