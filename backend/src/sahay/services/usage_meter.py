"""Usage metering — atomic increment-with-tier-check.

Stored per (user, period_type, period_start). Read on every action
and updated post-action. Race-safe via Postgres UPSERT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import UsagePeriod
from sahay.domain.tiers import get_limits


@dataclass(slots=True)
class UsageSnapshot:
    daily_count: int
    weekly_count: int
    monthly_count: int
    daily_limit: int
    weekly_limit: int
    monthly_image_gens_used: int
    monthly_image_gens_limit: int


def _today() -> date:
    return datetime.now(UTC).date()


def _week_start(d: date) -> date:
    return d.fromordinal(d.toordinal() - d.weekday())


def _month_start(d: date) -> date:
    return d.replace(day=1)


async def get_snapshot(
    db: AsyncSession, user_id: UUID, tier: str
) -> UsageSnapshot:
    today = _today()
    week = _week_start(today)
    month = _month_start(today)

    stmt = select(UsagePeriod).where(
        UsagePeriod.user_id == user_id,
        UsagePeriod.period_start.in_([today, week, month]),
    )
    rows = (await db.execute(stmt)).scalars().all()
    by_key = {(r.period_type, r.period_start): r for r in rows}

    limits = get_limits(tier)

    return UsageSnapshot(
        daily_count=by_key.get(("day", today)).action_count if by_key.get(("day", today)) else 0,
        weekly_count=by_key.get(("week", week)).action_count if by_key.get(("week", week)) else 0,
        monthly_count=by_key.get(("month", month)).action_count if by_key.get(("month", month)) else 0,
        daily_limit=limits.daily_actions,
        weekly_limit=limits.weekly_actions,
        monthly_image_gens_used=by_key.get(("month", month)).image_gens if by_key.get(("month", month)) else 0,
        monthly_image_gens_limit=limits.monthly_image_gens,
    )


async def check_action_allowed(
    db: AsyncSession, user_id: UUID, tier: str
) -> tuple[bool, str | None]:
    """Return (allowed, reason_if_not). Free tier hard-stops; paid tiers go to overage."""
    snap = await get_snapshot(db, user_id, tier)
    limits = get_limits(tier)

    if snap.daily_count >= snap.daily_limit:
        if limits.overage_per_action_paise == 0:
            return False, "daily_limit_reached"
    if snap.weekly_count >= snap.weekly_limit:
        if limits.overage_per_action_paise == 0:
            return False, "weekly_limit_reached"
    return True, None


async def increment_action(
    db: AsyncSession,
    user_id: UUID,
    *,
    tokens: int = 0,
    cost_paise: int = 0,
    is_image_gen: bool = False,
) -> None:
    """Atomically bump day + week + month counters via UPSERT.

    Uses Postgres ON CONFLICT to avoid race between SELECT and INSERT.
    """
    today = _today()
    periods = [
        ("day", today),
        ("week", _week_start(today)),
        ("month", _month_start(today)),
    ]

    for period_type, period_start in periods:
        stmt = pg_insert(UsagePeriod).values(
            user_id=user_id,
            period_type=period_type,
            period_start=period_start,
            action_count=1,
            tokens_used=tokens,
            cost_inr_paise=cost_paise,
            image_gens=1 if is_image_gen else 0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "period_type", "period_start"],
            set_={
                "action_count": UsagePeriod.action_count + 1,
                "tokens_used": UsagePeriod.tokens_used + tokens,
                "cost_inr_paise": UsagePeriod.cost_inr_paise + cost_paise,
                "image_gens": UsagePeriod.image_gens + (1 if is_image_gen else 0),
            },
        )
        await db.execute(stmt)
