"""Agent endpoint — one-step-at-a-time phone driver.

POST /v1/agent/step
  → Client sends current screen observation + goal + history
  → Claude picks the next tool call
  → Client executes it, loops back

Rate-limited per user. Tokens + cost tracked via usage_meter.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.domain.payment_blocklist import is_payment_app
from sahay.schemas.agent import AgentStepRequest, AgentStepResponse
from sahay.services import agent_engine, usage_meter
from sahay.services.rate_limiter import rate_limit
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1/agent", tags=["agent"])
log = get_logger(__name__)


@router.post(
    "/step",
    response_model=AgentStepResponse,
    dependencies=[Depends(rate_limit("agent", per_minute=240))],
)
async def agent_step(
    body: AgentStepRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> AgentStepResponse:
    """Ask Claude for the next action given the current phone screen."""
    obs = body.observation

    # Defense in depth: backend blocks payment apps even if Android sends one.
    if obs.app_package and is_payment_app(obs.app_package):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="payment_app_blocked",
        )

    # Enforce usage tier.
    allowed, reason = await usage_meter.check_action_allowed(db, user.id, user.tier)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason or "limit_reached",
        )

    result = await agent_engine.step(obs, user_id=str(user.id))

    # Meter the call.
    await usage_meter.increment_action(
        db, user.id,
        tokens=result.tokens_used,
        cost_paise=result.cost_inr_paise,
    )

    return AgentStepResponse(
        action=result.action,
        model=result.model,
        tokens_used=result.tokens_used,
        cost_inr_paise=result.cost_inr_paise,
    )
