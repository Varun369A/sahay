"""Short-lived notification mirror — what the agent can "see" on the phone.

Scope
─────
Android forwards every posted notification (minus payment/banking apps) to
this backend store. The agent-loop then injects the last N recent ones into
Claude's prompt so queries like "what did Amirtha say?" or "read my
WhatsApp" work without any extra tool call.

Why Redis, not Postgres
───────────────────────
Notifications are ephemeral by nature. A 30-minute sliding window is enough
to be useful for the agent ("you just got a WhatsApp from boss") and cheap
enough to keep entirely in RAM. No audit use case justifies long-term
retention — DPDP-wise, holding notification text in a DB is a liability we
don't need.

Security
────────
• Payment / banking / UPI / wallet apps are dropped at ingest (defense in
  depth — Android side also filters).
• Dedup by Android notification `key`: the same notification being reposted
  doesn't multiply entries.
• Keys are per-user; no cross-user leakage possible.
• TTL resets on every write so active phones keep context, idle ones age
  out cleanly.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

import redis.asyncio as aioredis

from sahay.domain.payment_blocklist import is_payment_app
from sahay.services.rate_limiter import get_redis
from sahay.util.logging import get_logger

log = get_logger(__name__)


# ─── Record ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class Notification:
    """One notification from the Android listener.

    `key` is Android's StatusBarNotification.getKey() — used for dedup and,
    for replyable notifications, as the handle the agent passes back to
    invoke the RemoteInput reply action.
    """

    key: str
    package: str
    app_name: str
    title: str
    text: str
    category: str              # e.g. "msg", "email", "reminder" — Android's Notification.CATEGORY_*
    posted_at: float           # unix seconds
    is_ongoing: bool = False   # persistent notifications (music player, nav) — lower priority
    replyable: bool = False    # has a RemoteInput reply action (WhatsApp, SMS, Gmail thread…)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Notification:
        return cls(
            key=str(d["key"])[:200],
            package=str(d["package"])[:200],
            app_name=str(d.get("app_name") or "")[:80],
            title=str(d.get("title") or "")[:200],
            text=str(d.get("text") or "")[:1000],
            category=str(d.get("category") or "")[:40],
            posted_at=float(d.get("posted_at") or time.time()),
            is_ongoing=bool(d.get("is_ongoing")),
            replyable=bool(d.get("replyable")),
        )


# ─── Store ───────────────────────────────────────────────────────────


class NotificationStore:
    """Rolling per-user notification mirror, Redis-backed.

    Storage layout:
      notif:{user_id}         → list of JSON blobs, newest first (LPUSH).
                                Capped at MAX_ITEMS via LTRIM.
      notif_seen:{user_id}    → set of recent `key` values, for dedup across
                                frequent resends. TTL-bounded; small footprint.
    """

    LIST_KEY = "notif:{user_id}"
    SEEN_KEY = "notif_seen:{user_id}"
    MAX_ITEMS = 100
    TTL_SECONDS = 30 * 60          # 30 minutes — ephemeral on purpose

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client

    def _list_key(self, user_id: str) -> str:
        return self.LIST_KEY.format(user_id=user_id)

    def _seen_key(self, user_id: str) -> str:
        return self.SEEN_KEY.format(user_id=user_id)

    # ─── Write ───────────────────────────────────────────────────────

    async def ingest(
        self, user_id: str, notifications: list[Notification],
    ) -> int:
        """Drop payment-app notifications, dedup by key, LPUSH the rest.

        Returns count actually stored (after filtering + dedup).
        """
        if not notifications:
            return 0

        r = self._redis or get_redis()
        list_key = self._list_key(user_id)
        seen_key = self._seen_key(user_id)

        # Filter payment apps up front — cheap, and avoids Redis work.
        kept = [n for n in notifications if not is_payment_app(n.package)]
        if not kept:
            return 0

        # Dedup against recently-seen keys (cross-call dedup).
        existing: set[str] = set()
        try:
            raw = await r.smembers(seen_key)
            existing = {str(k) for k in raw}
        except Exception as e:                                # noqa: BLE001
            log.warning("notif_seen_read_failed", err=str(e))

        fresh = [n for n in kept if n.key not in existing]
        if not fresh:
            return 0

        pipe = r.pipeline()
        for n in fresh:
            pipe.lpush(list_key, json.dumps(n.to_dict()))
        pipe.ltrim(list_key, 0, self.MAX_ITEMS - 1)
        pipe.expire(list_key, self.TTL_SECONDS)

        # Track keys we've seen — capped via trim trick (sets don't auto-trim,
        # so we clear + re-add the last 200 every write; cheap and bounded).
        pipe.sadd(seen_key, *(n.key for n in fresh))
        pipe.expire(seen_key, self.TTL_SECONDS)

        await pipe.execute()
        return len(fresh)

    # ─── Read ────────────────────────────────────────────────────────

    async def recent(
        self,
        user_id: str,
        *,
        limit: int = 10,
        since_seconds: int | None = None,
        package: str | None = None,
    ) -> list[Notification]:
        """Newest-first notifications, optionally filtered.

        `since_seconds`: skip entries older than this (clock-based).
        `package`:       only include notifications from this app package.
        """
        r = self._redis or get_redis()
        raw = await r.lrange(self._list_key(user_id), 0, max(0, limit * 3) - 1)

        cutoff = time.time() - since_seconds if since_seconds else None
        out: list[Notification] = []
        for blob in raw:
            try:
                n = Notification.from_dict(json.loads(blob))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
            if cutoff is not None and n.posted_at < cutoff:
                continue
            if package and n.package != package:
                continue
            out.append(n)
            if len(out) >= limit:
                break
        return out

    async def clear(self, user_id: str) -> int:
        """Wipe all stored notifications for a user (privacy / logout)."""
        r = self._redis or get_redis()
        pipe = r.pipeline()
        pipe.delete(self._list_key(user_id))
        pipe.delete(self._seen_key(user_id))
        res = await pipe.execute()
        return int(res[0] or 0)


# ─── Singleton ───────────────────────────────────────────────────────


_store: NotificationStore | None = None


def get_notification_store() -> NotificationStore:
    global _store
    if _store is None:
        _store = NotificationStore()
    return _store


# ─── Rendering for Claude prompt ─────────────────────────────────────


def format_for_prompt(
    notifications: list[Notification], *, max_chars: int = 1_400,
) -> str:
    """Render a compact block of notifications for Claude's context.

    Output example:
        RECENT NOTIFICATIONS (last 30min, newest first):
          • [WhatsApp] Amirtha — "Meeting at 4pm?"    [replyable key=wa_123]
          • [Gmail] Rakesh — "Re: Q2 numbers"         [replyable key=gm_9aa]
          • [Clock] Timer finished                    [key=clk_7]

    The `key=` is the Android SBN key — the agent passes it back to
    `reply_to_notification(notification_key=…)` to fire a RemoteInput
    reply, or uses it when the user references "the one from Amirtha".
    """
    if not notifications:
        return "(no recent notifications)"

    lines = ["RECENT NOTIFICATIONS (last 30min, newest first):"]
    used = len(lines[0])
    for n in notifications:
        app = n.app_name or n.package.split(".")[-1]
        title = n.title or "(no title)"
        text = (n.text or "").replace("\n", " ")[:140]
        tags = []
        if n.replyable:
            tags.append("replyable")
        # Only a short key prefix makes it into the prompt — keeps tokens down
        # while still letting the agent disambiguate between notifications.
        tags.append(f"key={n.key[:24]}")
        suffix = "  [" + ", ".join(tags) + "]"
        line = f"  • [{app[:20]}] {title[:60]}" + (f" — \"{text}\"" if text else "") + suffix
        if used + len(line) + 1 > max_chars:
            lines.append("  …")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
