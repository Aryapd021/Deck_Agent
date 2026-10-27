from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.errors import ApiError, ErrorCode
from app.core.jobs import event_stream
from app.db import get_session
from app.models.tables import JobRow

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}/events")
async def get_job_events(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    # 404 NOT_FOUND via ApiError if the JobRow is missing, checked BEFORE streaming
    job = session.get(JobRow, job_id)
    if not job:
        raise ApiError(ErrorCode.NOT_FOUND, f"Job {job_id} not found")

    # Parse the Last-Event-ID header to int (ignore if invalid)
    last_event_id: int | None = None
    header_val = request.headers.get("last-event-id")
    if header_val:
        try:
            last_event_id = int(header_val.strip())
        except (ValueError, TypeError):
            last_event_id = None

    return StreamingResponse(
        event_stream(job_id, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
