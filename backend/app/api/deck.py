from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.errors import ApiError, ErrorCode
from app.db import get_session
from app.models.api import DeckResponse
from app.models.deck import Deck
from app.models.tables import DeckVersionRow, ProjectRow

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
