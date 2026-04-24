"""Open-loop follow-up memory — "I'll do X", "remind me to Y".

Scope
─────
Short-lived commitments the user makes in conversation that should bubble
back up later. Distinct from long-term memory (durable facts) and distinct
from conversation memory (rolling per-session turns). These are the
"open TODOs" the agent surfaces in the morning briefing or when the
context calls for a nudge.

Storage
───────
Redis list keyed `followups:{user_id}`. Each entry is a compact JSON blob:
    {id, text, created_at, source}

Capped at MAX_ITEMS via LTRIM. TTL 7 days — after a week, an unactioned
"I'll reply to boss" is stale enough to drop rather than guilt-trip the
user about.

Not Postgres because:
  • These are low-value if lost (worst case the user gets zero nudges).
  • Write volume is bursty (a chatty user could add 5 in a minute).
  • Eviction semantics (time + count cap) fit Redis cheaply.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import redis.asyncio as aioredis

from sahay.services.rate_limiter import get_redis
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class FollowUp:
    id: str
    text: str
    created_at: float
    source: str = "agent"        # "agent" | "user" (future: manual add)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FollowUp:
        return cls(
            id=str(d["id"])[:64],
            text=str(d["text"])[:400],
            created_at=float(d.get("created_at") or time.time()),
            source=str(d.get("source") or "agent")[:16],
        )


class FollowUpStore:
    KEY_FMT = "followups:{user_id}"
    MAX_ITEMS = 25
    TTL_SECONDS = 7 * 24 * 3600       # week-long shelf life

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client

    def _key(self, user_id: str) -> str:
        return self.KEY_FMT.format(user_id=user_id)

    async def add(self, user_id: str, text: str, *, source: str = "agent") -> FollowUp:
        r = self._redis or get_redis()
        fu = FollowUp(
            id=uuid.uuid4().hex[:12],
            text=text.strip()[:400],
            created_at=time.time(),
            source=source,
        )
        key = self._key(user_id)
        pipe = r.pipeline()
        pipe.lpush(key, json.dumps(fu.to_dict()))
        pipe.ltrim(key, 0, self.MAX_ITEMS - 1)
        pipe.expire(key, self.TTL_SECONDS)
        await pipe.execute()
        return fu

    async def list(self, user_id: str, *, limit: int = 10) -> list[FollowUp]:
        r = self._redis or get_redis()
        raw = await r.lrange(self._key(user_id), 0, max(0, limit) - 1)
        out: list[FollowUp] = []
        for blob in raw:
            try:
                out.append(FollowUp.from_dict(json.loads(blob)))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
        return out

    async def remove(self, user_id: str, follow_up_id: str) -> bool:
        """Remove one follow-up by id. Returns True if something was removed.

        Redis LREM is O(N) on the list length which is capped at 25 — fine.
        """
        r = self._redis or get_redis()
        raw = await r.lrange(self._key(user_id), 0, -1)
        for blob in raw:
            try:
                fu = FollowUp.from_dict(json.loads(blob))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
            if fu.id == follow_up_id:
                removed = await r.lrem(self._key(user_id), 1, blob)
                return bool(removed)
        return False

    async def clear(self, user_id: str) -> None:
        r = self._redis or get_redis()
        await r.delete(self._key(user_id))


_store: FollowUpStore | None = None


def get_follow_up_store() -> FollowUpStore:
    global _store
    if _store is None:
        _store = FollowUpStore()
    return _store


def format_for_prompt(items: list[FollowUp], *, max_chars: int = 800) -> str:
    """Render a compact block for the agent / briefing prompt."""
    if not items:
        return "(no open follow-ups)"
    now = time.time()
    lines = ["OPEN FOLLOW-UPS (ordered newest first):"]
    used = len(lines[0])
    for fu in items:
        hours = max(0, int((now - fu.created_at) // 3600))
        when = f"{hours}h ago" if hours < 48 else f"{hours // 24}d ago"
        line = f"  • [{when}] {fu.text[:200]}"
        if used + len(line) + 1 > max_chars:
            lines.append("  …")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
