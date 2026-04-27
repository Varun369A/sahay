"""Unit tests for the reliability tracker — Bayesian math + bucket mapping.

The full DB-integration test (real `record_outcome` against a test
database) lives separately; here we lock down the scoring formula and
the outcome → counter mapping, since those are the parts that have to
stay numerically stable across every release.
"""

from __future__ import annotations

import pytest

from sahay.services.reliability_tracker import _BUCKETS, _bayesian_score


def test_zero_runs_starts_at_one_half() -> None:
    """Brand-new playbook has no signal → 0.5 (Laplace prior)."""
    assert _bayesian_score(0, 0) == pytest.approx(0.5)


def test_all_success_converges_to_one() -> None:
    assert _bayesian_score(1, 0) == pytest.approx(2 / 3)
    assert _bayesian_score(10, 0) == pytest.approx(11 / 12)
    assert _bayesian_score(100, 0) == pytest.approx(101 / 102)
    assert _bayesian_score(10_000, 0) > 0.999


def test_all_fail_converges_to_zero() -> None:
    assert _bayesian_score(0, 1) == pytest.approx(1 / 3)
    assert _bayesian_score(0, 10) == pytest.approx(1 / 12)
    assert _bayesian_score(0, 100) == pytest.approx(1 / 102)


def test_score_in_range_for_arbitrary_inputs() -> None:
    for s, f in [(0, 0), (1, 1), (50, 50), (999, 1), (1, 999), (100_000, 50_000)]:
        score = _bayesian_score(s, f)
        assert 0.0 <= score <= 1.0


def test_score_monotonic_in_success() -> None:
    """Holding fail constant, more successes → strictly higher score."""
    fail = 5
    last = -1.0
    for s in range(0, 50):
        score = _bayesian_score(s, fail)
        assert score > last
        last = score


def test_score_monotonic_in_fail() -> None:
    """Holding success constant, more failures → strictly lower score."""
    success = 5
    last = 2.0
    for f in range(0, 50):
        score = _bayesian_score(success, f)
        assert score < last
        last = score


def test_outcome_buckets_cover_documented_set() -> None:
    """The mapping table is the public contract — make sure every wire
    string we promise on the server actually has a row."""
    expected_keys = {
        "success", "expect_failed", "failed",
        "give_up", "max_steps", "aborted",
    }
    assert expected_keys == set(_BUCKETS.keys())


def test_only_success_counts_as_success() -> None:
    for outcome, (is_succ, _, _) in _BUCKETS.items():
        if outcome == "success":
            assert is_succ is True
        else:
            assert is_succ is False


def test_aborted_does_not_move_score() -> None:
    is_succ, is_fail, is_expect = _BUCKETS["aborted"]
    assert (is_succ, is_fail, is_expect) == (False, False, False)


def test_expect_failed_counts_as_both_fail_and_expect_fail() -> None:
    is_succ, is_fail, is_expect = _BUCKETS["expect_failed"]
    assert (is_succ, is_fail, is_expect) == (False, True, True)


def test_plain_fail_does_not_count_as_expect_fail() -> None:
    """expect_fail must remain a strict subset of fail so the playbook
    CHECK CONSTRAINT (expect_fail_count <= fail_count) can never be
    violated by the application."""
    for outcome in ("failed", "give_up", "max_steps"):
        is_succ, is_fail, is_expect = _BUCKETS[outcome]
        assert is_fail is True
        assert is_expect is False
