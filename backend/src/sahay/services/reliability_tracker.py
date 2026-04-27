"""Closed-loop reliability tracker for playbooks.

For each playbook run that ends, we:

  1. Append a `PlaybookRunOutcome` row (per-run telemetry).
  2. Increment Bayesian counters on the parent `Playbook`:
       success → success_count++
       expect_failed → fail_count++ AND expect_fail_count++
       any other failure → fail_count++
  3. Recompute `reliability_score = (success + 1) / (success + fail + 2)`.

Why Bayesian (Laplace's rule of succession): a brand-new playbook with
0 runs gets score 0.5 (unknown), not 0 (looks broken) or 1 (looks
perfect). After one success it's 2/3, after ten successes 11/12. This
matches the operational reality that we trust the score more as we see
more data.

Outcome buckets (free-text on the wire so we can extend without
migrations) — but the success/fail mapping below is the contract:

  outcome_str        | counted as
  -------------------+------------------
  "success"          | success
  "expect_failed"    | fail (and expect_fail)
  "failed"           | fail
  "give_up"          | fail
  "max_steps"        | fail
  "aborted"          | NEITHER — user / blocklist abort, not a reliability signal
  unknown            | NEITHER — log + ignore so we don't poison the score
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook, PlaybookRevision, PlaybookRunOutcome
from sahay.services.failure_classifier import (
    FailureClass,
    classify_from_reason,
)
from sahay.util.logging import get_logger

log = get_logger(__name__)


# Outcome → (counts_as_success, counts_as_fail, counts_as_expect_fail)
_BUCKETS: dict[str, tuple[bool, bool, bool]] = {
    "success":        (True,  False, False),
    "expect_failed":  (False, True,  True),
    "failed":         (False, True,  False),
    "give_up":        (False, True,  False),
    "max_steps":      (False, True,  False),
    "aborted":        (False, False, False),
}


@dataclass(slots=True)
class ReliabilityUpdate:
    playbook_id: UUID
    outcome: str
    new_success: int
    new_fail: int
    new_expect_fail: int
    new_score: float


async def record_outcome(
    db: AsyncSession,
    *,
    user_id: UUID,
    playbook_id: UUID,
    outcome: str,
    failed_step_index: int | None = None,
    failed_reason: str | None = None,
    duration_ms: int = 0,
    revision_id: UUID | None = None,
) -> ReliabilityUpdate | None:
    """Append the outcome row and Bayesian-update the playbook counters.

    Returns the post-update snapshot; or None if the outcome bucket is
    one we deliberately don't score (`aborted`, unknown).
    """
    outcome = outcome.strip()[:32]
    bucket = _BUCKETS.get(outcome)

    # Classify only when the run actually failed. Success and aborted
    # rows have NULL failure_class so dashboards can `WHERE failure_class
    # IS NOT NULL` to count failures cleanly.
    cls: str | None = None
    if outcome != "success" and outcome != "aborted":
        cls = classify_from_reason(failed_reason).value

    if bucket is None:
        log.warning("reliability_unknown_outcome", outcome=outcome)
        # Still record the row for forensics, but don't move the score.
        db.add(
            PlaybookRunOutcome(
                user_id=user_id, playbook_id=playbook_id,
                outcome=outcome, failed_step_index=failed_step_index,
                failed_reason=(failed_reason or None) and failed_reason[:200],
                failure_class=cls,
                duration_ms=max(0, int(duration_ms)),
            )
        )
        await db.flush()
        return None

    counts_success, counts_fail, counts_expect_fail = bucket

    # Lock the playbook row so concurrent outcomes don't interleave.
    pb = await db.scalar(
        select(Playbook).where(Playbook.id == playbook_id).with_for_update()
    )
    if pb is None:
        log.warning("reliability_playbook_missing", playbook_id=str(playbook_id))
        return None

    # Canary attribution: when this run was served from a canary spec,
    # the canary's counters move — NOT the parent's. This keeps the
    # parent's `parent_score_baseline` snapshot a fair "before" measure
    # for the promotion decision.
    canary_rev: PlaybookRevision | None = None
    if revision_id is not None:
        canary_rev = await db.scalar(
            select(PlaybookRevision)
            .where(PlaybookRevision.id == revision_id)
            .with_for_update()
        )
        if canary_rev is None or canary_rev.playbook_id != playbook_id:
            log.warning(
                "reliability_canary_revision_mismatch",
                revision_id=str(revision_id), playbook_id=str(playbook_id),
            )
            canary_rev = None

    if canary_rev is not None and canary_rev.status == "canary":
        if counts_success:
            canary_rev.success_count += 1
        if counts_fail:
            canary_rev.fail_count += 1
        # Parent counters frozen during canary window for this run.
        new_score = float(pb.reliability_score)
    else:
        if counts_success:
            pb.success_count += 1
        if counts_fail:
            pb.fail_count += 1
        if counts_expect_fail:
            pb.expect_fail_count += 1
        new_score = _bayesian_score(pb.success_count, pb.fail_count)
        pb.reliability_score = new_score

    db.add(
        PlaybookRunOutcome(
            user_id=user_id, playbook_id=playbook_id,
            outcome=outcome, failed_step_index=failed_step_index,
            failed_reason=(failed_reason or None) and failed_reason[:200],
            failure_class=cls,
            duration_ms=max(0, int(duration_ms)),
        )
    )
    await db.flush()

    log.info(
        "reliability_updated",
        playbook_id=str(playbook_id),
        outcome=outcome,
        failure_class=cls,
        success=pb.success_count, fail=pb.fail_count,
        expect_fail=pb.expect_fail_count,
        score=round(new_score, 3),
    )
    return ReliabilityUpdate(
        playbook_id=playbook_id,
        outcome=outcome,
        new_success=pb.success_count,
        new_fail=pb.fail_count,
        new_expect_fail=pb.expect_fail_count,
        new_score=new_score,
    )


def _bayesian_score(success: int, fail: int) -> float:
    """Laplace's rule of succession: (s + 1) / (s + f + 2). Always in [0, 1]."""
    total = success + fail
    return (success + 1) / (total + 2)
