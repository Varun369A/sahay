"""Tests for the auto-promotion verdict logic.

The decider runs without LLM calls; it's pure arithmetic + thresholds.
This test pins every decision branch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sahay.services.canary_decider import (
    MAX_AGE_HOURS,
    MIN_CANARY_RUNS,
    PROMOTE_DELTA,
    ROLLBACK_DELTA,
    _verdict_for,
)
from sahay.services.canary_router import WINDOW_HOURS


def _rev(
    *,
    success: int = 0,
    fail: int = 0,
    baseline: float = 0.50,
    age_hours: float = 1.0,
):
    """Quack-typed PlaybookRevision for the decider's pure verdict fn."""
    now = datetime.now(tz=timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        success_count=success,
        fail_count=fail,
        parent_score_baseline=baseline,
        created_at=now - timedelta(hours=age_hours),
    )


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ─── Hold branches ──────────────────────────────────────────────────


def test_hold_when_below_min_runs_and_inside_window() -> None:
    rev = _rev(success=2, fail=1, age_hours=1.0)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "hold"
    assert "insufficient_runs" in v.reason


def test_hold_when_close_to_baseline() -> None:
    """Enough runs, but the canary score is too close to baseline to
    declare a winner either way."""
    # 50/50 on 100 runs → bayes ≈ 0.50, baseline 0.50 → hold
    rev = _rev(success=50, fail=50, baseline=0.50)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "hold"
    assert v.reason == "too_close_to_baseline"


# ─── Promote ───────────────────────────────────────────────────────


def test_promote_when_significantly_better() -> None:
    # 90% success on 100 runs → bayes ≈ 0.892, baseline 0.50 → big win
    rev = _rev(success=90, fail=10, baseline=0.50)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "promote"
    assert v.canary_score - v.parent_baseline >= PROMOTE_DELTA


def test_promote_at_exactly_threshold() -> None:
    """A canary exactly at baseline + PROMOTE_DELTA should promote (>=)."""
    # 11 success, 0 fail → bayes = 12/13 ≈ 0.923. baseline must be 0.873
    # to put the canary exactly at threshold (0.873 + 0.05 = 0.923).
    rev = _rev(success=11, fail=0, baseline=0.873)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "promote"


# ─── Rollback ───────────────────────────────────────────────────────


def test_rollback_when_significantly_worse() -> None:
    # 10% success on 100 runs → bayes ≈ 0.108, baseline 0.50 → big loss
    rev = _rev(success=10, fail=90, baseline=0.50)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "rollback"
    assert v.parent_baseline - v.canary_score >= ROLLBACK_DELTA


def test_rollback_when_no_data_after_window() -> None:
    """If the canary window has closed AND we've still got fewer than
    MIN_CANARY_RUNS, it's not getting traffic — roll it back."""
    rev = _rev(success=2, fail=0, age_hours=WINDOW_HOURS + 5)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "rollback"
    assert "no_data_after_window" in v.reason


def test_rollback_at_max_age_regardless_of_score() -> None:
    """Even a beautifully-performing canary that somehow stalled past
    MAX_AGE gets rolled back — we don't allow zombie canaries."""
    rev = _rev(success=99, fail=1, baseline=0.50, age_hours=MAX_AGE_HOURS + 1)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "rollback"
    assert "max_age_exceeded" in v.reason


# ─── Edge cases ────────────────────────────────────────────────────


def test_zero_runs_inside_window_holds() -> None:
    rev = _rev(success=0, fail=0, age_hours=2.0)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "hold"
    assert "insufficient_runs" in v.reason


def test_min_runs_threshold_is_hard_cutoff() -> None:
    rev = _rev(success=MIN_CANARY_RUNS - 1, fail=0)
    v = _verdict_for(rev, now=_now())
    assert v.decision == "hold"


def test_no_baseline_uses_laplace_default() -> None:
    """If parent_score_baseline is None (legacy data), the decider falls
    back to 0.5 — same as the Laplace prior. A canary that beats 0.55
    should still promote."""
    rev = _rev(success=80, fail=10, baseline=None)
    v = _verdict_for(rev, now=_now())
    assert v.parent_baseline == pytest.approx(0.5)
    assert v.decision == "promote"


def test_verdict_round_trips_revision_id() -> None:
    rev = _rev(success=5, fail=2)
    v = _verdict_for(rev, now=_now())
    assert v.revision_id == rev.id
