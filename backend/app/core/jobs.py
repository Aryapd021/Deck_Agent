import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
from typing import Any, AsyncIterator, Awaitable, Callable, Literal
from uuid import uuid4

from sqlmodel import func, select

from app.core.errors import ApiError, ErrorCode
from app.core.storage import (
    finalize_version_dir,
    new_temp_version_dir,
    rel_to_storage,
    template_pptx_path,
)
from app.db import session_scope
from app.models.api import JobEvent, TemplateManifest
from app.models.deck import Deck
from app.models.facts import ChatDigest, ProjectFacts
from app.models.tables import (
    ArtifactRow,
    DeckVersionRow,
    JobEventRow,
    JobRow,
    ProjectInputRow,
    ProjectRow,
    TemplateRow,
)
from app.services.chat_digest import digest
from app.services.diagram import get_palette, render_diagram
from app.services.extract import build_deck, extract_facts
from app.services.ingest import build_repo_digest
from app.services.preview import generate_previews
from app.services.render import render_deck
from app.services.template_inspector import extract_palette
from app.services.validate import validate_deck

logger = logging.getLogger(__name__)

# Module-level state
_project_locks: dict[str, asyncio.Lock] = {}
_running_tasks: set[asyncio.Task[Any]] = set()
_job_subscribers: dict[str, set[asyncio.Queue[JobEvent]]] = {}
_job_seqs: dict[str, int] = {}
_job_events_cache: dict[str, dict[int, JobEvent]] = {}
_job_versions: dict[str, int | None] = {}


def _get_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]


async def try_acquire(project_id: str) -> None:
    """Acquires lock for a project non-blockingly, raising JOB_IN_PROGRESS if locked."""
    lock = _get_lock(project_id)
    if lock.locked():
        raise ApiError(ErrorCode.JOB_IN_PROGRESS, "A job is already running for this project")
    await lock.acquire()


def release(project_id: str) -> None:
    """Releases the lock for project_id if held."""
    lock = _project_locks.get(project_id)
    if lock and lock.locked():
        lock.release()


@dataclass
class JobResult:
    version: int | None
    message: str


class JobContext:
    def __init__(self, job_id: str, project_id: str) -> None:
        self.job_id = job_id
        self.project_id = project_id
        self.current_stage: str | None = None

    async def emit(
        self,
        stage: str,
        status: Literal["started", "progress", "done", "error"] | str,
        message: str,
        version: int | None = None,
    ) -> None:
        self.current_stage = stage

        # Determine next seq (in-memory counter initialized from DB max(seq))
        if self.job_id not in _job_seqs:
            with session_scope() as session:
                stmt = select(func.max(JobEventRow.seq)).where(JobEventRow.job_id == self.job_id)
                max_seq = session.exec(stmt).first()
                _job_seqs[self.job_id] = max_seq if max_seq is not None else 0

        _job_seqs[self.job_id] += 1
        seq = _job_seqs[self.job_id]

        # FIRST write the JobEventRow (short sync session, commit)
        # Also update JobRow.stage
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            event_row = JobEventRow(
                job_id=self.job_id,
                seq=seq,
                stage=stage,
                status=str(status),
                message=message,
                ts=now,
            )
            session.add(event_row)
            job = session.get(JobRow, self.job_id)
            if job:
                job.stage = stage
                session.add(job)

        event = JobEvent(
            seq=seq,
            stage=stage,
            status=status,  # type: ignore
            message=message,
            version=version,
        )

        _job_events_cache.setdefault(self.job_id, {})[seq] = event
        if version is not None:
            _job_versions[self.job_id] = version

        # THEN push the JobEvent to every subscriber queue in module dict
        queues = _job_subscribers.get(self.job_id, set()).copy()
        for q in queues:
            q.put_nowait(event)


async def _run(
    job_id: str,
    project_id: str,
    runner: Callable[[JobContext], Awaitable[JobResult]],
) -> None:
    ctx = JobContext(job_id=job_id, project_id=project_id)
    final_status = "done"
    error_code: str | None = None

    try:
        result = await runner(ctx)
        await ctx.emit(
            stage="done",
            status="done",
            message=result.message,
            version=result.version,
        )
    except ApiError as exc:
        final_status = "error"
        error_code = exc.code.value if isinstance(exc.code, ErrorCode) else str(exc.code)
        stage = ctx.current_stage or "error"
        await ctx.emit(
            stage=stage,
            status="error",
            message=exc.message,
        )
    except Exception:
        final_status = "error"
        error_code = None
        logger.exception("Job %s failed with unhandled exception", job_id)
        stage = ctx.current_stage or "error"
        await ctx.emit(
            stage=stage,
            status="error",
            message="Internal error",
        )
    finally:
        now = datetime.now(timezone.utc)
        try:
            with session_scope() as session:
                job = session.get(JobRow, job_id)
                if job:
                    job.status = final_status
                    job.stage = ctx.current_stage or ("done" if final_status == "done" else "error")
                    job.error_code = error_code
                    job.ended_at = now
                    session.add(job)
        except Exception:
            logger.exception("Failed to update JobRow in finally for job %s", job_id)
        finally:
            release(project_id)


async def start_job(
    project_id: str,
    job_type: str,
    runner: Callable[[JobContext], Awaitable[JobResult]],
) -> str:
    """Starts a background job. The caller must ALREADY hold the project lock."""
    job_id = uuid4().hex
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        job = JobRow(
            id=job_id,
            project_id=project_id,
            type=job_type,
            status="running",
            stage=None,
            error_code=None,
            started_at=now,
            ended_at=None,
        )
        session.add(job)

    task = asyncio.create_task(_run(job_id, project_id, runner))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

    return job_id


async def event_stream(
    job_id: str,
    last_event_id: int | None = None,
    ping_interval: float = 15.0,
) -> AsyncIterator[str]:
    """Streams SSE events for a job, with replay starting from last_event_id."""
    # Subscribe a queue FIRST
    queue: asyncio.Queue[JobEvent] = asyncio.Queue()
    if job_id not in _job_subscribers:
        _job_subscribers[job_id] = set()
    _job_subscribers[job_id].add(queue)

    try:
        min_seq = last_event_id if last_event_id is not None and last_event_id > 0 else 0

        # Read stored events from DB and materialize as JobEvent while session is open
        with session_scope() as session:
            stmt = (
                select(JobEventRow)
                .where(JobEventRow.job_id == job_id, JobEventRow.seq > min_seq)
                .order_by(JobEventRow.seq.asc())
            )
            rows = session.exec(stmt).all()
            stored_events: list[JobEvent] = []
            for row in rows:
                cached_event = _job_events_cache.get(job_id, {}).get(row.seq)
                if cached_event is not None:
                    stored_events.append(cached_event)
                else:
                    version = _job_versions.get(job_id) if row.stage == "done" else None
                    stored_events.append(
                        JobEvent(
                            seq=row.seq,
                            stage=row.stage,
                            status=row.status,  # type: ignore
                            message=row.message,
                            version=version,
                        )
                    )

            job_row = session.get(JobRow, job_id)
            job_already_finished = (
                job_row is not None and job_row.status in ("done", "error")
            )

        last_sent = min_seq
        terminal_reached = False

        # Replay stored events
        for event in stored_events:
            last_sent = max(last_sent, event.seq)
            yield f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"
            if event.stage == "done" or event.status == "error":
                terminal_reached = True
                break

        # Drain any items already queued during the DB query
        while not queue.empty():
            queued_event = queue.get_nowait()
            if queued_event.seq > last_sent:
                last_sent = max(last_sent, queued_event.seq)
                yield f"id: {queued_event.seq}\ndata: {queued_event.model_dump_json()}\n\n"
                if queued_event.stage == "done" or queued_event.status == "error":
                    terminal_reached = True
                    break

        # If terminal reached or job finished, finish without waiting
        if terminal_reached or job_already_finished:
            return

        # Live streaming loop
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=ping_interval)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue

            if event.seq <= last_sent:
                continue

            last_sent = max(last_sent, event.seq)
            yield f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"

            if event.stage == "done" or event.status == "error":
                break

    finally:
        # Unsubscribe in finally
        if job_id in _job_subscribers:
            _job_subscribers[job_id].discard(queue)
            if not _job_subscribers[job_id]:
                _job_subscribers.pop(job_id, None)


async def commit_version(
    project_id: str,
    version: int,
    deck: Deck,
    created_by: str,
    summary: str | None,
    tmp_dir: Path,
    facts: ProjectFacts | None = None,
) -> None:
    final_dir: Path | None = None
    try:
        # (1) in a thread write deck.json (deck.model_dump_json) and, if facts is given, facts.json into tmp_dir
        def _write_json_files() -> None:
            (tmp_dir / "deck.json").write_text(deck.model_dump_json(), encoding="utf-8")
            if facts is not None:
                (tmp_dir / "facts.json").write_text(facts.model_dump_json(), encoding="utf-8")

        await asyncio.to_thread(_write_json_files)

        # (2) finalize_version_dir(tmp_dir, project_id, version)
        final_dir = finalize_version_dir(tmp_dir, project_id, version)

        # (3) in ONE DB transaction insert the DeckVersionRow and ArtifactRow rows
        artifacts: list[ArtifactRow] = []

        deck_pptx = final_dir / "deck.pptx"
        if deck_pptx.exists():
            artifacts.append(
                ArtifactRow(
                    project_id=project_id,
                    version=version,
                    kind="pptx",
                    path=rel_to_storage(deck_pptx),
                )
            )

        diagram_png = final_dir / "diagram.png"
        if diagram_png.exists():
            artifacts.append(
                ArtifactRow(
                    project_id=project_id,
                    version=version,
                    kind="diagram",
                    path=rel_to_storage(diagram_png),
                )
            )

        slides_dir = final_dir / "slides"
        if slides_dir.exists() and slides_dir.is_dir():
            for slide_path in sorted(slides_dir.glob("*.png")):
                artifacts.append(
                    ArtifactRow(
                        project_id=project_id,
                        version=version,
                        kind="slide_png",
                        path=rel_to_storage(slide_path),
                    )
                )

        now = datetime.now(timezone.utc)
        with session_scope() as session:
            v_row = DeckVersionRow(
                project_id=project_id,
                version=version,
                deck_json=deck.model_dump_json(),
                created_by=created_by,
                summary=summary,
                created_at=now,
            )
            session.add(v_row)
            for art in artifacts:
                session.add(art)

    except Exception:
        if final_dir is not None and final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        elif tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


async def run_generate(ctx: JobContext) -> JobResult:
    with session_scope() as session:
        project = session.get(ProjectRow, ctx.project_id)
        if not project:
            raise ApiError(ErrorCode.NOT_FOUND, f"Project {ctx.project_id} not found")

        template = session.get(TemplateRow, project.template_id)
        if not template:
            raise ApiError(ErrorCode.NOT_FOUND, f"Template {project.template_id} not found")

        repo_ref = project.repo_ref
        template_id = project.template_id
        manifest = TemplateManifest.model_validate_json(template.manifest_json)

        input_row = session.get(ProjectInputRow, ctx.project_id)
        pitch_notes = input_row.pitch_notes if input_row else ""
        chat_redacted = input_row.chat_redacted if input_row else ""
        criteria = input_row.criteria if input_row else ""

        stmt = (
            select(func.max(DeckVersionRow.version))
            .where(DeckVersionRow.project_id == ctx.project_id)
        )
        max_v = session.exec(stmt).first()
        version = (max_v or 0) + 1

    # 1. ingest: build_repo_digest(project.repo_ref)
    await ctx.emit(stage="ingest", status="started", message="Analyzing repository...")
    repo_digest = await build_repo_digest(repo_ref)
    await ctx.emit(
        stage="ingest",
        status="done",
        message=f"Analyzed {repo_digest.name} ({len(repo_digest.files_included)} files)",
    )

    # 2. redact: no processing; emit from the stored chat_redacted ("Chat sanitized on upload" or "No chat provided")
    await ctx.emit(stage="redact", status="started", message="Checking chat redactions...")
    redact_msg = (
        "Chat sanitized on upload"
        if chat_redacted and chat_redacted.strip()
        else "No chat provided"
    )
    await ctx.emit(stage="redact", status="done", message=redact_msg)

    # 3. digest: digest(chat_redacted) (empty chat -> empty ChatDigest, message "skipped")
    await ctx.emit(stage="digest", status="started", message="Synthesizing chat digest...")
    if not chat_redacted or not chat_redacted.strip():
        chat_digest = ChatDigest(
            problem_framing=[], decisions=[], rejected_options=[], insights=[]
        )
        digest_msg = "skipped"
    else:
        chat_digest = await digest(chat_redacted)
        digest_msg = f"Extracted {len(chat_digest.decisions)} decisions"
    await ctx.emit(stage="digest", status="done", message=digest_msg)

    # 4. facts: extract_facts(repo_digest, pitch_notes, chat_digest, criteria)
    await ctx.emit(stage="facts", status="started", message="Extracting project facts...")
    facts = await extract_facts(repo_digest, pitch_notes, chat_digest, criteria)
    await ctx.emit(stage="facts", status="done", message=f"Extracted facts for {facts.name}")

    # 5. deck: build_deck(...), then validate_deck(deck, manifest, known_paths=set(repo_digest.known_paths)); emit each warning as a progress event with stage "deck"
    await ctx.emit(stage="deck", status="started", message="Drafting presentation...")
    deck = await build_deck(facts, manifest, project_id=ctx.project_id, version=version)
    report = validate_deck(deck, manifest, known_paths=set(repo_digest.known_paths))
    deck = report.deck
    for w in report.warnings:
        await ctx.emit(stage="deck", status="progress", message=w)
    await ctx.emit(stage="deck", status="done", message=f"Drafted {len(deck.slides)} slides")

    tmp_dir = new_temp_version_dir(ctx.project_id, version)
    try:
        tmpl_path = template_pptx_path(template_id)

        # 6. diagram: render_diagram into tmp_dir
        await ctx.emit(stage="diagram", status="started", message="Rendering architecture diagram...")
        palette = get_palette(manifest)
        if manifest.palette is None:
            lazy_palette = extract_palette(tmpl_path)
            if lazy_palette:
                palette = get_palette({"palette": lazy_palette})

        if deck.diagram is not None:
            await asyncio.to_thread(render_diagram, deck.diagram, palette, tmp_dir)
            await ctx.emit(stage="diagram", status="done", message="Rendered architecture diagram")
        else:
            await ctx.emit(stage="diagram", status="done", message="skipped")

        # 7. render: pass diagram_png=tmp_dir/"diagram.png" to render_deck
        await ctx.emit(stage="render", status="started", message="Rendering PowerPoint deck...")
        out_pptx = tmp_dir / "deck.pptx"
        diagram_png = tmp_dir / "diagram.png"
        await asyncio.to_thread(
            render_deck,
            deck,
            manifest,
            tmpl_path,
            out_pptx,
            diagram_png if diagram_png.exists() else None,
        )
        await ctx.emit(stage="render", status="done", message="Rendered PowerPoint presentation")

        # 8. preview: generate_previews(tmp_dir/"deck.pptx", tmp_dir)
        await ctx.emit(stage="preview", status="started", message="Generating slide previews...")
        try:
            previews = await asyncio.to_thread(generate_previews, out_pptx, tmp_dir)
            if not previews:
                await ctx.emit(stage="preview", status="done", message="skipped")
            else:
                await ctx.emit(stage="preview", status="done", message=f"Generated {len(previews)} slide previews")
        except Exception as e:
            logger.warning("Preview generation failed: %s", e)
            await ctx.emit(stage="preview", status="done", message="skipped")

        # commit_version
        await commit_version(
            project_id=ctx.project_id,
            version=version,
            deck=deck,
            created_by="generate",
            summary=f"Generated deck from {repo_digest.name}",
            tmp_dir=tmp_dir,
            facts=facts,
        )
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return JobResult(version=version, message=f"Deck v{version} ready")
