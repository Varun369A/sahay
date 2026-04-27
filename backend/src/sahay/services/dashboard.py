"""Read-only aggregations for the admin dashboard.

One module = the entire investor-grade ops view. Every helper is a pure
SQL aggregation against tables that already exist (no new telemetry
introduced — this builds on Tasks 4–11).

Headline numbers are over a configurable lookback window (default 7d).
All queries are bounded so a growing outcomes table doesn't slow this
down — required because the dashboard refreshes ~once/minute in the
admin UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook, PlaybookRevision, PlaybookRunOutcome
from sahay.services.canary_decider import _verdict_for
from sahay.services.reliability_tracker import _bayesian_score


DEFAULT_WINDOW_DAYS = 7


# ─── Result shapes ──────────────────────────────────────────────────


@dataclass(slots=True)
class Headline:
    window_days: int
    total_runs: int
    successes: int
    failures: int
    aborted: int
    success_rate: float                     # successes / (successes + failures)
    mean_success_duration_ms: int           # average over successful runs only
    distinct_users: int
    distinct_playbooks: int


@dataclass(slots=True)
class IntentRow:
    app_package: str
    app_name: str
    intent: str
    reliability_score: float
    success_count_lifetime: int
    fail_count_lifetime: int
    runs_in_window: int
    successes_in_window: int
    success_rate_in_window: float           # 0..1, NaN-free (returns 0 when no runs)


@dataclass(slots=True)
class FailureClassRow:
    failure_class: str
    count: int
    pct_of_failures: float                  # 0..1


@dataclass(slots=True)
class CanaryRow:
    revision_id: str
    playbook_id: str
    app_package: str
    intent: str
    version: int
    canary_score: float
    parent_baseline: float
    canary_runs: int
    age_hours: float
    live_decision: str                      # what the decider WOULD do right now


@dataclass(slots=True)
class RecentPromotion:
    revision_id: str
    playbook_id: str
    app_package: str
    intent: str
    promoted_at: datetime
    notes: str | None


@dataclass(slots=True)
class DashboardSnapshot:
    generated_at: datetime
    headline: Headline
    intents: list[IntentRow] = field(default_factory=list)
    failure_classes: list[FailureClassRow] = field(default_factory=list)
    canaries: list[CanaryRow] = field(default_factory=list)
    recent_promotions: list[RecentPromotion] = field(default_factory=list)


# ─── Aggregations ──────────────────────────────────────────────────


def _cutoff(window_days: int) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=window_days)


async def headline(db: AsyncSession, *, window_days: int = DEFAULT_WINDOW_DAYS) -> Headline:
    cutoff = _cutoff(window_days)
    base = select(PlaybookRunOutcome).where(PlaybookRunOutcome.created_at >= cutoff)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    successes = await db.scalar(
        select(func.count())
        .select_from(base.where(PlaybookRunOutcome.outcome == "success").subquery())
    ) or 0
    failures = await db.scalar(
        select(func.count())
        .select_from(
            base.where(
                PlaybookRunOutcome.outcome.in_(
                    ("failed", "expect_failed", "give_up", "max_steps")
                )
            ).subquery()
        )
    ) or 0
    aborted = await db.scalar(
        select(func.count())
        .select_from(base.where(PlaybookRunOutcome.outcome == "aborted").subquery())
    ) or 0

    mean_success_dur = await db.scalar(
        select(func.coalesce(func.avg(PlaybookRunOutcome.duration_ms), 0))
        .where(PlaybookRunOutcome.created_at >= cutoff)
        .where(PlaybookRunOutcome.outcome == "success")
    ) or 0

    distinct_users = await db.scalar(
        select(func.count(func.distinct(PlaybookRunOutcome.user_id)))
        .where(PlaybookRunOutcome.created_at >= cutoff)
    ) or 0
    distinct_pbs = await db.scalar(
        select(func.count(func.distinct(PlaybookRunOutcome.playbook_id)))
        .where(PlaybookRunOutcome.created_at >= cutoff)
    ) or 0

    denom = successes + failures
    success_rate = float(successes) / denom if denom else 0.0

    return Headline(
        window_days=window_days,
        total_runs=int(total),
        successes=int(successes),
        failures=int(failures),
        aborted=int(aborted),
        success_rate=success_rate,
        mean_success_duration_ms=int(mean_success_dur),
        distinct_users=int(distinct_users),
        distinct_playbooks=int(distinct_pbs),
    )


async def per_intent_reliability(
    db: AsyncSession, *,
    window_days: int = DEFAULT_WINDOW_DAYS, limit: int = 50,
) -> list[IntentRow]:
    """Per-playbook table sorted by (lifetime score asc, recent runs desc)
    so the admin lands on "the worst-performing playbooks getting the
    most traffic" — the natural triage order."""
    cutoff = _cutoff(window_days)
    runs_in_window = func.count(PlaybookRunOutcome.id).label("runs_w")
    succ_in_window = func.sum(
        case((PlaybookRunOutcome.outcome == "success", 1), else_=0)
    ).label("succ_w")

    sub = (
        select(
            PlaybookRunOutcome.playbook_id,
            runs_in_window,
            succ_in_window,
        )
        .where(PlaybookRunOutcome.created_at >= cutoff)
        .group_by(PlaybookRunOutcome.playbook_id)
        .subquery()
    )
    stmt = (
        select(
            Playbook.app_package, Playbook.app_name, Playbook.intent,
            Playbook.reliability_score,
            Playbook.success_count, Playbook.fail_count,
            func.coalesce(sub.c.runs_w, 0),
            func.coalesce(sub.c.succ_w, 0),
        )
        .outerjoin(sub, sub.c.playbook_id == Playbook.id)
        .where(Playbook.is_payment_app.is_(False))
        .order_by(Playbook.reliability_score.asc(), desc(func.coalesce(sub.c.runs_w, 0)))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[IntentRow] = []
    for app_pkg, app_name, intent, score, s_lt, f_lt, runs_w, succ_w in rows:
        rate = (float(succ_w) / float(runs_w)) if runs_w else 0.0
        out.append(IntentRow(
            app_package=app_pkg, app_name=app_name, intent=intent,
            reliability_score=float(score),
            success_count_lifetime=int(s_lt),
            fail_count_lifetime=int(f_lt),
            runs_in_window=int(runs_w),
            successes_in_window=int(succ_w),
            success_rate_in_window=rate,
        ))
    return out


async def top_failure_classes(
    db: AsyncSession, *,
    window_days: int = DEFAULT_WINDOW_DAYS, limit: int = 10,
) -> list[FailureClassRow]:
    cutoff = _cutoff(window_days)
    stmt = (
        select(
            PlaybookRunOutcome.failure_class,
            func.count().label("n"),
        )
        .where(PlaybookRunOutcome.created_at >= cutoff)
        .where(PlaybookRunOutcome.failure_class.isnot(None))
        .group_by(PlaybookRunOutcome.failure_class)
        .order_by(desc("n"))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    total = sum(int(r[1]) for r in rows) or 1
    return [
        FailureClassRow(
            failure_class=str(cls), count=int(n),
            pct_of_failures=float(n) / float(total),
        )
        for cls, n in rows
    ]


async def active_canaries(db: AsyncSession) -> list[CanaryRow]:
    """List in-flight canaries with the verdict the decider WOULD render
    if it ran right now. Lets the admin preview promotions before the
    cron applies them."""
    stmt = (
        select(PlaybookRevision, Playbook)
        .join(Playbook, Playbook.id == PlaybookRevision.playbook_id)
        .where(PlaybookRevision.status == "canary")
        .order_by(desc(PlaybookRevision.created_at))
    )
    rows = (await db.execute(stmt)).all()
    now = datetime.now(tz=timezone.utc)
    out: list[CanaryRow] = []
    for rev, pb in rows:
        verdict = _verdict_for(rev, now=now)
        runs = rev.success_count + rev.fail_count
        score = _bayesian_score(rev.success_count, rev.fail_count)
        baseline = (
            float(rev.parent_score_baseline)
            if rev.parent_score_baseline is not None else 0.5
        )
        age_hours = (now - rev.created_at).total_seconds() / 3600.0
        out.append(CanaryRow(
            revision_id=str(rev.id),
            playbook_id=str(rev.playbook_id),
            app_package=pb.app_package,
            intent=pb.intent,
            version=rev.version,
            canary_score=score,
            parent_baseline=baseline,
            canary_runs=runs,
            age_hours=age_hours,
            live_decision=verdict.decision,
        ))
    return out


async def recent_promotions(
    db: AsyncSession, *,
    window_days: int = DEFAULT_WINDOW_DAYS, limit: int = 20,
) -> list[RecentPromotion]:
    cutoff = _cutoff(window_days)
    stmt = (
        select(PlaybookRevision, Playbook)
        .join(Playbook, Playbook.id == PlaybookRevision.playbook_id)
        .where(PlaybookRevision.status == "promoted")
        .where(PlaybookRevision.promoted_at >= cutoff)
        .order_by(desc(PlaybookRevision.promoted_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        RecentPromotion(
            revision_id=str(rev.id),
            playbook_id=str(rev.playbook_id),
            app_package=pb.app_package,
            intent=pb.intent,
            promoted_at=rev.promoted_at,            # type: ignore[arg-type]
            notes=rev.notes,
        )
        for rev, pb in rows
    ]


async def snapshot(
    db: AsyncSession, *, window_days: int = DEFAULT_WINDOW_DAYS,
) -> DashboardSnapshot:
    """Single read returning the full dashboard payload."""
    return DashboardSnapshot(
        generated_at=datetime.now(tz=timezone.utc),
        headline=await headline(db, window_days=window_days),
        intents=await per_intent_reliability(db, window_days=window_days),
        failure_classes=await top_failure_classes(db, window_days=window_days),
        canaries=await active_canaries(db),
        recent_promotions=await recent_promotions(db, window_days=window_days),
    )


# ─── Pure helpers exposed for unit tests ────────────────────────────


def safe_rate(successes: int, total: int) -> float:
    """Success rate that's NaN-free at zero total. Stable across the
    headline / intent-row / class-pct call sites — keep them in sync
    by going through this."""
    return float(successes) / float(total) if total else 0.0
