import asyncio
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.jobs import (
    JobContext,
    JobResult,
    _job_events_cache,
    _job_seqs,
    _job_subscribers,
    _job_versions,
    _project_locks,
    _running_tasks,
    event_stream,
    release,
    start_job,
    try_acquire,
)
from app.db import get_engine, init_db, session_scope
from app.main import app
from app.models.tables import JobEventRow, JobRow, ProjectRow, TemplateRow


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)

    import app.db as db_mod
    db_mod._engine = None
    init_db()

    # Clear in-memory job tracking structures
    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()

    yield

    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()
    db_mod._engine = None


def _create_project(project_id: str = "proj_1") -> None:
    with session_scope() as session:
        if not session.get(TemplateRow, "tmpl_1"):
            tmpl = TemplateRow(
                id="tmpl_1",
                name="Default Template",
                path="templates/tmpl_1.pptx",
                manifest_json="{}",
            )
            session.add(tmpl)
            session.flush()
        if not session.get(ProjectRow, project_id):
            proj = ProjectRow(
                id=project_id,
                name="Test Project",
                repo_ref="./",
                template_id="tmpl_1",
            )
            session.add(proj)


@pytest.mark.asyncio
async def test_try_acquire_and_release():
    project_id = "p_lock_test"

    # First acquire succeeds
    await try_acquire(project_id)

    # Second acquire while held raises JOB_IN_PROGRESS
    with pytest.raises(ApiError) as exc_info:
        await try_acquire(project_id)
    assert exc_info.value.code == ErrorCode.JOB_IN_PROGRESS
    assert "already running" in exc_info.value.message

    # Works again after release
    release(project_id)
    await try_acquire(project_id)
    release(project_id)


@pytest.mark.asyncio
async def test_events_persisted_before_subscriber_sees_them():
    project_id = "p_seq_test"
    _create_project(project_id)

    await try_acquire(project_id)
    job_id = "job_seq_test"
    ctx = JobContext(job_id=job_id, project_id=project_id)

    # Insert JobRow
    with session_scope() as session:
        job = JobRow(id=job_id, project_id=project_id, type="generate", status="running")
        session.add(job)

    # Add subscriber queue
    sub_queue = asyncio.Queue()
    _job_subscribers.setdefault(job_id, set()).add(sub_queue)

    # Emit event 1
    await ctx.emit(stage="ingest", status="progress", message="Cloning repo")
    event1 = await sub_queue.get()
    assert event1.seq == 1
    assert event1.stage == "ingest"
    assert event1.message == "Cloning repo"

    # Assert DB row exists BEFORE or AS subscriber processes it
    with Session(get_engine()) as session:
        db_row1 = session.exec(
            select(JobEventRow).where(JobEventRow.job_id == job_id, JobEventRow.seq == 1)
        ).first()
        assert db_row1 is not None
        assert db_row1.seq == 1
        assert db_row1.stage == "ingest"
        assert db_row1.message == "Cloning repo"

    # Emit event 2
    await ctx.emit(stage="facts", status="progress", message="Extracting facts")
    event2 = await sub_queue.get()
    assert event2.seq == 2

    with Session(get_engine()) as session:
        db_row2 = session.exec(
            select(JobEventRow).where(JobEventRow.job_id == job_id, JobEventRow.seq == 2)
        ).first()
        assert db_row2 is not None
        assert db_row2.seq == 2
        assert db_row2.stage == "facts"

    release(project_id)


@pytest.mark.asyncio
async def test_event_stream_replays_after_last_event_id():
    project_id = "p_replay_test"
    _create_project(project_id)

    job_id = "job_replay"
    ctx = JobContext(job_id=job_id, project_id=project_id)

    with session_scope() as session:
        job = JobRow(id=job_id, project_id=project_id, type="generate", status="running")
        session.add(job)

    # Emit 4 events + 1 terminal event
    stages = ["ingest", "redact", "digest", "facts", "done"]
    for i, st in enumerate(stages, 1):
        status = "done" if st == "done" else "progress"
        await ctx.emit(stage=st, status=status, message=f"Step {i}", version=1 if st == "done" else None)

    with session_scope() as session:
        j = session.get(JobRow, job_id)
        if j:
            j.status = "done"
            session.add(j)

    # Replay from last_event_id = 2 -> Should only receive seq 3, 4, 5
    chunks = [chunk async for chunk in event_stream(job_id, last_event_id=2)]
    assert len(chunks) == 3
    assert "id: 3" in chunks[0]
    assert "digest" in chunks[0]
    assert "id: 4" in chunks[1]
    assert "facts" in chunks[1]
    assert "id: 5" in chunks[2]
    assert "done" in chunks[2]


@pytest.mark.asyncio
async def test_runner_api_error_produces_error_event_and_sets_error_code():
    project_id = "p_api_err"
    _create_project(project_id)

    await try_acquire(project_id)

    async def fail_runner(ctx: JobContext) -> JobResult:
        await ctx.emit(stage="ingest", status="progress", message="Starting ingest")
        raise ApiError(ErrorCode.LLM_INVALID_OUTPUT, "Failed to parse structured output")

    job_id = await start_job(project_id, "generate", fail_runner)
    await asyncio.gather(*list(_running_tasks))

    with session_scope() as session:
        job = session.get(JobRow, job_id)
        assert job is not None
        assert job.status == "error"
        assert job.error_code == "LLM_INVALID_OUTPUT"
        assert job.ended_at is not None

        # Check that error event was emitted at the current stage
        events = session.exec(
            select(JobEventRow).where(JobEventRow.job_id == job_id).order_by(JobEventRow.seq.asc())
        ).all()
        assert len(events) == 2
        assert events[0].stage == "ingest"
        assert events[0].status == "progress"
        assert events[1].stage == "ingest"
        assert events[1].status == "error"
        assert events[1].message == "Failed to parse structured output"


@pytest.mark.asyncio
async def test_lock_released_after_both_success_and_failure():
    project_id = "p_release_test"
    _create_project(project_id)

    # 1. Success case
    await try_acquire(project_id)

    async def ok_runner(ctx: JobContext) -> JobResult:
        await ctx.emit(stage="render", status="progress", message="Rendering")
        return JobResult(version=1, message="Success")

    await start_job(project_id, "generate", ok_runner)
    await asyncio.gather(*list(_running_tasks))

    # Lock must be released
    await try_acquire(project_id)
    release(project_id)

    # 2. Failure with ApiError
    await try_acquire(project_id)

    async def api_err_runner(ctx: JobContext) -> JobResult:
        raise ApiError(ErrorCode.TEMPLATE_INVALID, "Invalid template")

    await start_job(project_id, "generate", api_err_runner)
    await asyncio.gather(*list(_running_tasks))

    # Lock must be released
    await try_acquire(project_id)
    release(project_id)

    # 3. Failure with unhandled Exception
    await try_acquire(project_id)

    async def crash_runner(ctx: JobContext) -> JobResult:
        raise RuntimeError("Something exploded")

    crash_job_id = await start_job(project_id, "generate", crash_runner)
    await asyncio.gather(*list(_running_tasks))

    # Lock must be released
    await try_acquire(project_id)
    release(project_id)

    with session_scope() as session:
        job = session.get(JobRow, crash_job_id)
        assert job is not None
        assert job.status == "error"
        assert job.error_code is None  # Generic internal error -> error_code NULL
        events = session.exec(
            select(JobEventRow).where(JobEventRow.job_id == crash_job_id)
        ).all()
        assert any(e.status == "error" and e.message == "Internal error" for e in events)


@pytest.mark.asyncio
async def test_event_stream_ends_after_terminal_event():
    project_id = "p_stream_term"
    _create_project(project_id)

    await try_acquire(project_id)

    async def runner(ctx: JobContext) -> JobResult:
        await ctx.emit(stage="ingest", status="started", message="Starting ingest")
        await asyncio.sleep(0.01)
        await ctx.emit(stage="render", status="progress", message="Rendering")
        await asyncio.sleep(0.01)
        return JobResult(version=2, message="All done")

    job_id = await start_job(project_id, "generate", runner)

    chunks = []
    async for chunk in event_stream(job_id):
        chunks.append(chunk)

    # The stream should have terminated cleanly
    assert len(chunks) == 3
    assert "ingest" in chunks[0]
    assert "render" in chunks[1]
    assert "done" in chunks[2]
    assert '"status":"done"' in chunks[2]
    assert '"version":2' in chunks[2]


@pytest.mark.asyncio
async def test_event_stream_idle_ping():
    _create_project("p_idle")
    job_id = "job_idle"
    with session_scope() as session:
        job = JobRow(id=job_id, project_id="p_idle", type="generate", status="running")
        session.add(job)

    # Pass very short ping_interval so we see the ping quickly
    gen = event_stream(job_id, ping_interval=0.05)
    first_chunk = await gen.__anext__()
    assert first_chunk == ": ping\n\n"
    await gen.aclose()


def test_api_jobs_events_endpoint():
    client = TestClient(app)

    # 404 for missing job
    res = client.get("/api/jobs/nonexistent_id/events")
    assert res.status_code == 404
    data = res.json()
    assert data["code"] == "NOT_FOUND"

    # Existing job with events
    project_id = "p_endpoint_test"
    _create_project(project_id)

    job_id = "job_endpoint"
    with session_scope() as session:
        job = JobRow(id=job_id, project_id=project_id, type="generate", status="done")
        session.add(job)
        session.flush()
        ev1 = JobEventRow(job_id=job_id, seq=1, stage="ingest", status="progress", message="msg1")
        ev2 = JobEventRow(job_id=job_id, seq=2, stage="done", status="done", message="msg2")
        session.add(ev1)
        session.add(ev2)

    res = client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "1"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["connection"] == "keep-alive"
    assert res.headers["x-accel-buffering"] == "no"
    body = res.text
    assert "id: 2" in body
    assert "id: 1" not in body
