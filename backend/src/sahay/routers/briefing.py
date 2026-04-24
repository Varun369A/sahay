"""Morning briefing endpoint.

GET /v1/agent/morning-briefing   — returns a one-sentence briefing the
Android unlock-trigger speaks aloud on first unlock of the day.

Rate limit is tight: the Android side throttles to once per calendar
day per user, but we enforce server-side too to cap Sonnet spend.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.services.briefing import compose
from sahay.services.rate_limiter import rate_limit

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class MorningBriefingResponse(BaseModel):
    text: str
    model: str
    tokens_used: int
    cost_inr_paise: int
    web_searches: int


@router.get(
    "/morning-briefing",
    response_model=MorningBriefingResponse,
    dependencies=[Depends(rate_limit("briefing", per_minute=2))],
)
async def morning_briefing(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> MorningBriefingResponse:
    briefing = await compose(
        db=db,
        user_id=str(user.id),
        assistant_name=user.display_name or "Sahay",
        role=user.role,
        locale=user.locale,
    )
    return MorningBriefingResponse(
        text=briefing.text,
        model=briefing.model,
        tokens_used=briefing.tokens_used,
        cost_inr_paise=briefing.cost_inr_paise,
        web_searches=briefing.web_searches,
    )
