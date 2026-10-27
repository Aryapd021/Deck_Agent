from contextlib import asynccontextmanager
from datetime import datetime, timezone
import shutil

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from app.api.artifacts import router as artifacts_router
from app.api.deck import router as deck_router
from app.api.jobs import router as jobs_router
from app.api.projects import router as projects_router
from app.api.templates import router as templates_router
from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.db import init_db, session_scope
from app.models.api import HealthResponse
from app.models.tables import JobRow


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB schema
    init_db()

    # Mark any JobRow still running as error (orphaned from a previous crash)
    with session_scope() as session:
        statement = select(JobRow).where(JobRow.status == "running")
        orphans = session.exec(statement).all()
        now = datetime.now(timezone.utc)
        for job in orphans:
            job.status = "error"
            job.error_code = "ORPHANED_JOB"
            job.ended_at = now
            session.add(job)

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DeckAgent",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register custom API error handlers
    register_exception_handlers(app)

    # Create API router under /api
    api_router = APIRouter(prefix="/api")

    @api_router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        current_settings = get_settings()
        dot_available = shutil.which("dot") is not None
        git_available = shutil.which("git") is not None
        soffice_available = shutil.which("soffice") is not None

        return HealthResponse(
            status="ok",
            dot=dot_available,
            git=git_available,
            soffice=soffice_available,
            preview_mode=current_settings.preview_mode,
            llm_provider=current_settings.llm_provider,
        )

    # Include five sub-routers
    api_router.include_router(templates_router)
    api_router.include_router(projects_router)
    api_router.include_router(jobs_router)
    api_router.include_router(deck_router)
    api_router.include_router(artifacts_router)

    app.include_router(api_router)

    # Mount static frontend if frontend_dist exists (mounted last so /api is never shadowed)
    if settings.frontend_dist.exists() and settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")

    return app


app = create_app()
