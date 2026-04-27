"""Canary traffic routing for playbook revisions.

When a playbook has an in-flight `PlaybookRevision(status='canary')`,
this module decides per-user whether THIS request gets the canary spec
or the live parent spec.

Design principles:

  • Sticky per user: a user is either in the canary bucket for the
    entire window or not at all. No flapping between specs across
    requests — that would taint the comparison and confuse the user.
  • Deterministic: bucketing is `sha256(user_id | playbook_id) % 100 <
    bucket_pct`. Same inputs → same answer; no DB hit on the hot path.
  • Time-windowed: a canary stops getting new traffic after `WINDOW_HOURS`,
    even if it's still in 'canary' state. The decider task then promotes
    or rolls back based on the data collected.
  • One canary per playbook: the router only looks at the most-recent
    canary. Stacked canaries are an antipattern (the improver already
    refuses to create one when an in-flight canary exists).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import PlaybookRevision

# How many of the 100 buckets the canary serves. 10 → ~10% of traffic.
BUCKET_PCT_DEFAULT = 10

# After this many hours the canary stops getting NEW traffic; users
# already in the bucket see the parent again. The decider task should
# run shortly after.
WINDOW_HOURS = 24


@dataclass(slots=True)
class CanaryDecision:
    """What route to take for one (user, playbook) request."""
    use_canary: bool
    revision_id: UUID | None
    spec_json: dict[str, Any] | None
    bucket: int                         # 0..99 — for logging / observability


def bucket_for(user_id: str, playbook_id: str) -> int:
    """Stable 0-99 bucket for a (user, playbook) pair."""
    h = hashlib.sha256(f"{user_id}|{playbook_id}".encode("utf-8")).digest()
    # First 4 bytes as unsigned int → mod 100. Plenty of entropy; the mod
    # bias at 100 vs 2^32 is negligible.
    n = int.from_bytes(h[:4], "big")
    return n % 100


async def pick_active_revision(
    db: AsyncSession,
    *,
    playbook_id: UUID,
    user_id: str,
    bucket_pct: int = BUCKET_PCT_DEFAULT,
    window_hours: int = WINDOW_HOURS,
) -> CanaryDecision:
    """Return the canary spec to serve, or signal "use parent".

    Cheap path:
      • no canary row → CanaryDecision(use_canary=False)
      • canary outside window → CanaryDecision(use_canary=False)
      • bucket >= bucket_pct → CanaryDecision(use_canary=False)

    Only when ALL three pass do we hand back the canary spec.
    """
    stmt = (
        select(PlaybookRevision)
        .where(PlaybookRevision.playbook_id == playbook_id)
        .where(PlaybookRevision.status == "canary")
        .order_by(desc(PlaybookRevision.created_at))
        .limit(1)
    )
    rev = (await db.execute(stmt)).scalar_one_or_none()

    user_bucket = bucket_for(user_id, str(playbook_id))
    if rev is None:
        return CanaryDecision(False, None, None, user_bucket)

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
    if rev.created_at < cutoff:
        return CanaryDecision(False, None, None, user_bucket)

    if user_bucket >= bucket_pct:
        return CanaryDecision(False, None, None, user_bucket)

    return CanaryDecision(
        use_canary=True,
        revision_id=rev.id,
        spec_json=rev.spec_json,
        bucket=user_bucket,
    )
