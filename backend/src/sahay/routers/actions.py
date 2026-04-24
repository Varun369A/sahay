"""Action planner + telemetry endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import ActionLog, User
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.domain.payment_blocklist import is_payment_app
from sahay.schemas.action import (
    ActionLogRequest,
    DispatchRequest,
    DispatchResponse,
    PlannedStepOut,
    PlanRequest,
    PlanResponse,
    ResolveRequest,
    ResolveResponse,
    UsageOut,
)
from sahay.services import action_planner, intent_dispatcher, step_resolver, usage_meter
from sahay.services.rate_limiter import rate_limit
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1/actions", tags=["actions"])
log = get_logger(__name__)


@router.post(
    "/plan",
    response_model=PlanResponse,
    dependencies=[Depends(rate_limit("actions", per_minute=120))],
)
async def plan_action(
    body: PlanRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> PlanResponse:
    # Hard rule #1: payment app blocklist.
    if is_payment_app(body.app_package):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="payment_app_blocked",
        )

    # Hard rule #2: tier enforcement.
    allowed, reason = await usage_meter.check_action_allowed(db, user.id, user.tier)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason or "limit_reached",
        )

    plan = await action_planner.plan(
        db,
        action_planner.PlanRequest(
            user_id=user.id,
            app_package=body.app_package,
            intent=body.intent,
            inputs=body.inputs,
            screen_tree_summary=body.screen_tree_summary,
            app_version=body.app_version,
        ),
    )

    # Pre-charge the meter at plan-time (prevents abuse where user spams plan
    # but reports never-success).
    await usage_meter.increment_action(
        db, user.id,
        tokens=plan.tokens_used,
        cost_paise=plan.cost_inr_paise,
    )

    return PlanResponse(
        steps=[
            PlannedStepOut(
                step=s.step, description=s.description, target_screen=s.target_screen,
                action=s.action, target_selector=s.target_selector, params=s.params,
                confirm_required=s.confirm_required, timeout_ms=s.timeout_ms,
                on_failure=s.on_failure,
            )
            for s in plan.steps
        ],
        source=plan.source,  # type: ignore[arg-type]
        reliability_score=plan.reliability_score,
        needs_per_step_confirm=plan.needs_per_step_confirm,
        requires_inputs=plan.requires_inputs,
        notes=plan.notes,
    )


@router.post(
    "/dispatch",
    response_model=DispatchResponse,
    dependencies=[Depends(rate_limit("dispatch", per_minute=120))],
)
async def dispatch_intent(
    body: DispatchRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> DispatchResponse:
    """Voice utterance → structured intent. Claude Haiku-powered, language-agnostic."""
    result = await intent_dispatcher.dispatch(db, body.utterance, body.locale)
    return DispatchResponse(
        matched=result.matched,
        app_package=result.app_package,
        intent=result.intent,
        inputs=result.inputs or {},
        confidence=result.confidence,
        notes=result.notes,
    )


@router.post(
    "/resolve",
    response_model=ResolveResponse,
    dependencies=[Depends(rate_limit("resolve", per_minute=120))],
)
async def resolve_step(
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> ResolveResponse:
    """Vision-grounded fallback when on-device selector misses.

    Sahay's PlaybookExecutor calls this when waitForNode times out — Claude
    reads the live accessibility tree and picks the right node.
    """
    if is_payment_app(body.app_package):
        raise HTTPException(403, "payment_app_blocked")

    result = await step_resolver.resolve(
        app_package=body.app_package,
        step_description=body.step_description,
        desired_action=body.desired_action,
        screen_tree_summary=body.screen_tree_summary,
        inputs=body.inputs,
    )
    return ResolveResponse(
        found=result.found,
        selector=result.selector,
        action=result.action,
        params=result.params,
        confidence=result.confidence,
        notes=result.notes,
    )


@router.post(
    "/log",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("actions_log", per_minute=240))],
)
async def log_action(
    body: ActionLogRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> None:
    """Telemetry: success/fail/abort signal from the device."""
    if is_payment_app(body.app_package):
        raise HTTPException(403, "payment_app_blocked")

    db.add(
        ActionLog(
            user_id=user.id,
            app_package=body.app_package,
            intent=body.intent,
            status=body.status,
            duration_ms=body.duration_ms,
            failure_reason=body.failure_reason,
            user_aborted=body.user_aborted,
        )
    )


# ─── Usage ───────────────────────────────────────────────────────────


usage_router = APIRouter(prefix="/v1/usage", tags=["usage"])


@usage_router.get("", response_model=UsageOut)
async def get_usage(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> UsageOut:
    snap = await usage_meter.get_snapshot(db, user.id, user.tier)
    return UsageOut(
        daily_count=snap.daily_count,
        daily_limit=snap.daily_limit,
        weekly_count=snap.weekly_count,
        weekly_limit=snap.weekly_limit,
        monthly_image_gens_used=snap.monthly_image_gens_used,
        monthly_image_gens_limit=snap.monthly_image_gens_limit,
        tier=user.tier,
    )
