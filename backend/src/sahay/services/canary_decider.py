"""Auto-promote or roll back canary playbook revisions.

Designed to run as a scheduled task shortly after the canary window
closes (24h after `created_at`). For each canary that has accumulated
enough samples, compares the canary's Bayesian score against the
snapshot of the parent score taken at canary-creation time:

  • canary score >= baseline + PROMOTE_DELTA      → promote
  • canary score <= baseline - ROLLBACK_DELTA     → rollback
  • otherwise (insufficient data, or close to par) → leave as canary
    (will be evaluated again next run, until window expires by `MAX_AGE`)

Why a delta and not just ">"? Random variance at small N looks like
improvement. We require a meaningful gap (default 5pp) to call a
patch a real win, and a smaller gap to call it a real regression.

Cost shape: pure SQL + arithmetic. No LLM calls. Effectively free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import PlaybookRevision
from sahay.services.canary_router import WINDOW_HOURS
from sahay.services.playbook_improver import (
    promote_revision,
    rollback_revision,
)
from sahay.services.reliability_tracker import _bayesian_score
from sahay.util.logging import get_logger

log = get_logger(__name__)


# Minimum runs the canary needs before we'll act on the comparison.
# Below this, a 0-sample win can look like 1.0 (Laplace) which is not
# a meaningful signal.
MIN_CANARY_RUNS = 10

# Score deltas required to trigger promotion / rollback. 5pp / 3pp keeps
# us conservative on accept and slightly more eager on reject.
PROMOTE_DELTA = 0.05
ROLLBACK_DELTA = 0.03

# Hard ceiling on how long a canary can sit. Past this, if we still
# can't decide, it's rolled back — a stalled canary should NOT linger
# forever blocking new improvers from running.
MAX_AGE_HOURS = 7 * 24


@dataclass(slots=True)
class CanaryVerdict:
    revision_id: UUID
    decision: Literal["promote", "rollback", "hold"]
    canary_score: float
    parent_baseline: float
    canary_total: int
    reason: str


def _verdict_for(rev: PlaybookRevision, *, now: datetime) -> CanaryVerdict:
    canary_total = rev.success_count + rev.fail_count
    canary_score = _bayesian_score(rev.success_count, rev.fail_count)
    baseline = float(rev.parent_score_baseline) if rev.parent_score_baseline else 0.5

    age_hours = (now - rev.created_at).total_seconds() / 3600.0

    # Hard timeout: stalled canaries get rolled back regardless of stats.
    if age_hours >= MAX_AGE_HOURS:
        return CanaryVerdict(
            revision_id=rev.id,
            decision="rollback",
            canary_score=canary_score, parent_baseline=baseline,
            canary_total=canary_total,
            reason=f"max_age_exceeded:{age_hours:.1f}h",
        )

    if canary_total < MIN_CANARY_RUNS:
        # Still gathering data — but only hold if we're inside the
        # serving window. Outside the window with no data → rollback,
        # the canary clearly isn't getting traffic.
        if age_hours > WINDOW_HOURS:
            return CanaryVerdict(
                revision_id=rev.id, decision="rollback",
                canary_score=canary_score, parent_baseline=baseline,
                canary_total=canary_total,
                reason=f"no_data_after_window:{canary_total}_runs",
            )
        return CanaryVerdict(
            revision_id=rev.id, decision="hold",
            canary_score=canary_score, parent_baseline=baseline,
            canary_total=canary_total,
            reason=f"insufficient_runs:{canary_total}/{MIN_CANARY_RUNS}",
        )

    if canary_score >= baseline + PROMOTE_DELTA:
        return CanaryVerdict(
            revision_id=rev.id, decision="promote",
            canary_score=canary_score, parent_baseline=baseline,
            canary_total=canary_total,
            reason=(
                f"canary_better_by_{canary_score - baseline:.3f}_"
                f"(>={PROMOTE_DELTA})"
            ),
        )
    if canary_score <= baseline - ROLLBACK_DELTA:
        return CanaryVerdict(
            revision_id=rev.id, decision="rollback",
            canary_score=canary_score, parent_baseline=baseline,
            canary_total=canary_total,
            reason=(
                f"canary_worse_by_{baseline - canary_score:.3f}_"
                f"(>={ROLLBACK_DELTA})"
            ),
        )

    return CanaryVerdict(
        revision_id=rev.id, decision="hold",
        canary_score=canary_score, parent_baseline=baseline,
        canary_total=canary_total,
        reason="too_close_to_baseline",
    )


async def evaluate_all_canaries(db: AsyncSession) -> list[CanaryVerdict]:
    """Walk every canary revision, compute a verdict, apply it.

    Returns the list of verdicts (decision recorded) for observability.
    """
    stmt = (
        select(PlaybookRevision)
        .where(PlaybookRevision.status == "canary")
    )
    revs = (await db.execute(stmt)).scalars().all()
    now = datetime.now(tz=timezone.utc)
    out: list[CanaryVerdict] = []
    for rev in revs:
        verdict = _verdict_for(rev, now=now)
        out.append(verdict)
        if verdict.decision == "promote":
            await promote_revision(db, revision_id=rev.id)
        elif verdict.decision == "rollback":
            await rollback_revision(db, revision_id=rev.id)
        # `hold` → no action, will revisit next run

        log.info(
            "canary_verdict",
            revision_id=str(rev.id),
            decision=verdict.decision,
            canary_score=round(verdict.canary_score, 3),
            baseline=round(verdict.parent_baseline, 3),
            runs=verdict.canary_total,
            reason=verdict.reason,
        )
    return out
