"""Tests for the selector_trainer pure logic.

The DB-touching `per_step_tier_histogram` needs a real session; we cover
its arithmetic via `step_recommendation` which is the decision function
the histogram feeds into.
"""

from __future__ import annotations

import pytest

from sahay.services.selector_trainer import (
    TIER_PRIORITY,
    step_recommendation,
    tier_priority,
)


# ─── tier_priority ──────────────────────────────────────────────────


def test_tier_priority_ordering() -> None:
    assert tier_priority("viewId") < tier_priority("text_exact")
    assert tier_priority("text_exact") < tier_priority("text_substring")
    assert tier_priority("cd_exact") < tier_priority("cd_substring")
    assert tier_priority("text_exact") < tier_priority("sibling_anchor")


def test_tier_priority_unknown_sorts_last() -> None:
    assert tier_priority("not_a_real_tier") == len(TIER_PRIORITY)


def test_tier_priority_all_canonical_tiers_present() -> None:
    """Future contract — adding a new tier name in the device code must
    be mirrored here. If a tier flows through `selector_trainer` but
    isn't in `TIER_PRIORITY`, every recommendation involving it is
    flagged as 'worse than primary' which would falsely promote nothing."""
    expected_tiers = {
        "viewId", "text_exact", "cd_exact", "text_substring",
        "cd_substring", "className", "sibling_anchor",
    }
    assert expected_tiers.issubset(set(TIER_PRIORITY))


# ─── step_recommendation: insufficient data ─────────────────────────


def test_recommendation_returns_none_below_min_samples() -> None:
    assert step_recommendation({"text_exact": 3}, primary_tier="text_exact", min_samples=5) is None


def test_recommendation_returns_none_for_empty_histogram() -> None:
    assert step_recommendation({}, primary_tier="text_exact") is None


# ─── step_recommendation: primary still winning ─────────────────────


def test_recommendation_silent_when_primary_winning() -> None:
    """If the playbook's stated primary tier is dominant, no recommendation
    fires — the spec is already healthy."""
    hist = {"text_exact": 18, "cd_substring": 2}
    assert step_recommendation(hist, primary_tier="text_exact") is None


def test_recommendation_silent_when_better_tier_winning() -> None:
    """If a HIGHER-priority tier is winning (somehow — maybe the
    playbook drifted to a worse primary), we silently accept that."""
    hist = {"viewId": 15, "cd_substring": 5}
    assert step_recommendation(hist, primary_tier="text_exact") is None


# ─── step_recommendation: drift detected ────────────────────────────


def test_recommendation_promotes_dominant_fallback() -> None:
    hist = {"text_exact": 2, "cd_substring": 18}
    rec = step_recommendation(hist, primary_tier="text_exact")
    assert rec is not None
    assert rec["winning_tier"] == "cd_substring"
    assert rec["share"] == pytest.approx(0.9)
    assert rec["n"] == 20
    assert "promote_cd_substring_over_text_exact" == rec["recommendation"]


def test_recommendation_silent_when_share_below_threshold() -> None:
    """A 50/50 split between primary and fallback isn't conclusive
    drift — wait for more data before recommending a swap."""
    hist = {"text_exact": 10, "cd_substring": 10}
    assert step_recommendation(
        hist, primary_tier="text_exact", drift_threshold=0.6,
    ) is None


def test_recommendation_no_primary_recorded_branch() -> None:
    """When the playbook spec doesn't tell us the primary (e.g. multiple
    selector keys), we still surface a dominant-winner recommendation
    so Opus can simplify the spec."""
    hist = {"text_substring": 9, "viewId": 1}
    rec = step_recommendation(hist, primary_tier=None)
    assert rec is not None
    assert rec["winning_tier"] == "text_substring"
    assert rec["recommendation"] == "promote_winner_no_primary_recorded"


@pytest.mark.parametrize(
    "hist,primary,expect_rec",
    [
        # Below min samples
        ({"viewId": 4}, "viewId", False),
        # Primary winning
        ({"viewId": 100, "text_substring": 5}, "viewId", False),
        # Fallback winning, share above threshold
        ({"viewId": 1, "text_substring": 20}, "viewId", True),
        # Fallback winning but share below threshold
        ({"viewId": 9, "text_substring": 11}, "viewId", False),
    ],
)
def test_recommendation_table(
    hist: dict[str, int], primary: str, expect_rec: bool,
) -> None:
    result = step_recommendation(hist, primary_tier=primary)
    assert (result is not None) == expect_rec
