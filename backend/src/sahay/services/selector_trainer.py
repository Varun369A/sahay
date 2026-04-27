"""Selector-tier aggregator for the inner reliability loop.

When a playbook step keeps succeeding via a non-primary selector tier
(e.g. the playbook says `text="Send"` but `cd_substring` is doing all the
work), the spec is fragile waiting to break — the moment the substring
match drifts, the step fails. The right move is to promote the actually-
winning tier into the primary selector.

This module turns the device's per-run `selector_tiers` JSON map into
that structured signal:

  per_step_tier_histogram(playbook_id) →
    { step_index: { tier: count, ... }, ... }

The Opus improver consumes the histogram alongside failure_class
counts. When `selector_stale` dominates AND the histogram shows a
fallback tier consistently winning, the patch becomes obvious — make
that tier the primary.

Pure SQL + dict aggregation. No LLM calls. Effectively free.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import PlaybookRunOutcome


# Tiers in priority order — earlier means "primary, expected to win".
# The trainer flags a step when a later tier is winning more than this
# fraction of the time AND the primary tier wins less than the floor.
TIER_PRIORITY: list[str] = [
    "viewId",
    "text_exact",
    "cd_exact",
    "text_substring",
    "cd_substring",
    "className",
    "sibling_anchor",
    "resolver",
    "fuzzy_keyword",
    "fuzzy_edittext",
    "selector",                   # generic / unknown — should be rare
]


def tier_priority(tier: str) -> int:
    """Lower is better. Unknown tiers sort to the end."""
    try:
        return TIER_PRIORITY.index(tier)
    except ValueError:
        return len(TIER_PRIORITY)


async def per_step_tier_histogram(
    db: AsyncSession,
    *,
    playbook_id: UUID,
    lookback_days: int = 14,
) -> dict[int, dict[str, int]]:
    """Aggregate winning tiers per step from the recent run history.

    Returns: {step_index: {tier_name: count}}
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    stmt = (
        select(PlaybookRunOutcome.selector_tiers)
        .where(PlaybookRunOutcome.playbook_id == playbook_id)
        .where(PlaybookRunOutcome.created_at >= cutoff)
        .where(PlaybookRunOutcome.selector_tiers.isnot(None))
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: dict[int, Counter[str]] = defaultdict(Counter)
    for tiers in rows:
        if not isinstance(tiers, dict):
            continue
        for step_str, tier in tiers.items():
            try:
                step = int(step_str)
            except (TypeError, ValueError):
                continue
            if not isinstance(tier, str):
                continue
            out[step][tier] += 1
    return {step: dict(counter) for step, counter in out.items()}


def step_recommendation(
    histogram: dict[str, int],
    *,
    primary_tier: str | None = None,
    min_samples: int = 5,
    drift_threshold: float = 0.6,
) -> dict[str, str | float | int] | None:
    """Render a per-step recommendation from one step's histogram.

    Returns None if there isn't enough data to recommend anything.

    Logic:
      • Determine the dominant winning tier (most counts).
      • If the dominant tier is BETTER (lower priority index) than the
        playbook's stated primary, we're already doing fine — return None.
      • If the dominant tier is WORSE than the primary AND its share
        meets `drift_threshold`, recommend promoting it.

    The result is a small dict suitable for inclusion in the Opus
    improver's prompt.
    """
    total = sum(histogram.values())
    if total < min_samples:
        return None
    winner, winner_n = max(histogram.items(), key=lambda kv: kv[1])
    share = winner_n / total

    # No primary specified: recommend the winner if its share is dominant.
    if primary_tier is None:
        if share < drift_threshold:
            return None
        return {
            "winning_tier": winner,
            "share": round(share, 2),
            "n": total,
            "recommendation": "promote_winner_no_primary_recorded",
        }

    if tier_priority(winner) <= tier_priority(primary_tier):
        # Primary or better is winning — nothing to do.
        return None
    if share < drift_threshold:
        return None
    return {
        "winning_tier": winner,
        "share": round(share, 2),
        "n": total,
        "recommendation": f"promote_{winner}_over_{primary_tier}",
    }
