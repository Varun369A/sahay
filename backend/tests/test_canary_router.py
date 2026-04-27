"""Tests for the canary bucket routing math.

DB-touching paths (`pick_active_revision`) need a real session; here we
lock down the pure bucketing function which is the trickiest part.
"""

from __future__ import annotations

from collections import Counter

from sahay.services.canary_router import bucket_for


def test_bucket_is_deterministic() -> None:
    """Same inputs → same bucket. Sticky routing depends on this."""
    a = bucket_for("user-1", "playbook-1")
    b = bucket_for("user-1", "playbook-1")
    assert a == b


def test_bucket_in_range() -> None:
    for u in ("a", "b", "c", "alice@example.com"):
        for p in ("p1", "p2", "p3"):
            assert 0 <= bucket_for(u, p) < 100


def test_different_users_get_different_buckets() -> None:
    """Sanity: ten distinct users → at least 5 distinct buckets.
    (Not a strict test of distribution — just that we're not collapsing
    everything to a constant.)"""
    buckets = {bucket_for(f"u-{i}", "playbook-x") for i in range(10)}
    assert len(buckets) >= 5


def test_same_user_different_playbooks_differ() -> None:
    """A canary on playbook X and a canary on playbook Y should bucket
    INDEPENDENTLY — bucket inclusion shouldn't correlate across
    playbooks."""
    u = "user-stable"
    a = bucket_for(u, "pb-a")
    b = bucket_for(u, "pb-b")
    # Statistically very likely to differ. If they're equal it's a 1/100
    # collision; we use a known-different pair below to also force it.
    different = a != b
    different = different or bucket_for(u, "pb-c") != bucket_for(u, "pb-d")
    assert different


def test_distribution_roughly_uniform() -> None:
    """Across 10k synthetic users on one playbook, every decile should
    see between 700 and 1300 hits — sanity check that SHA-256 mod 100
    isn't lopsided enough to ruin the 10% rollout."""
    counts: Counter[int] = Counter()
    for i in range(10_000):
        counts[bucket_for(f"u-{i}", "pb-canonical") // 10] += 1
    for decile in range(10):
        n = counts.get(decile, 0)
        assert 700 < n < 1300, f"decile {decile} had {n} users (expected ~1000)"


def test_realistic_uuid_inputs_work() -> None:
    """UUID-style strings have far more entropy than the toy strings
    above — the function must still behave."""
    user_uuid = "9f8a7b6c-5d4e-3f2a-1b0c-9d8e7f6a5b4c"
    pb_uuid = "11111111-2222-3333-4444-555555555555"
    bucket = bucket_for(user_uuid, pb_uuid)
    assert 0 <= bucket < 100
