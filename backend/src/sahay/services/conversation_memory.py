"""Per-conversation short-term memory.

Scope
─────
Holds the rolling context of ONE agent session (usually a single task,
sometimes a multi-task dialogue). Keyed by (user_id, conversation_id).

This is the "last 10 turns" that make Claude behave like a coherent
assistant across multiple utterances — *"what's the weather"* followed
by *"and tomorrow?"* works because turn 2 sees turn 1's context.

Not to be confused with long-term memory (cross-session facts about
the user). That lives in Postgres and is loaded via a different path.

Storage
───────
Redis list keyed `convmem:{user_id}:{conversation_id}`. Each element
is a compact JSON blob of a single turn. LTRIM keeps the last N turns
to bound token cost. TTL resets on every write so active conversations
stick around, idle ones disappear after an hour.

Token-budget management
───────────────────────
Raw tool args (especially screen_tree_summary) would blow up token
count fast. We store an abbreviated form per turn: goal + tool + key
args + outcome. Full observations are NEVER persisted — they're
re-captured next turn from the live device.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

import redis.asyncio as aioredis

from sahay.services.rate_limiter import get_redis
from sahay.util.logging import get_logger

log = get_logger(__name__)


# ─── Turn record ─────────────────────────────────────────────────────


@dataclass(slots=True)
class Turn:
    """One agent step, stripped to essentials."""

    ts: float                             # unix seconds
    step: int                             # step index in this conversation
    tool: str                             # "tap_node" / "type_in_node" / "speak" / …
    args_summary: str                     # compact human-readable (e.g. "node 7, text='Dune'")
    outcome: str                          # "ok" / "done" / "failed: …"
    reasoning: str | None = None          # Claude's 1-line rationale


# ─── Conversation memory API ─────────────────────────────────────────


class ConversationMemory:
    """Rolling per-session turn log, Redis-backed."""

    KEY_FMT = "convmem:{user_id}:{conversation_id}"
    MAX_TURNS = 20                        # hard cap — LTRIM enforces this
    TTL_SECONDS = 60 * 60                 # 1-hour sliding expiration
    META_FMT = "convmem_meta:{user_id}:{conversation_id}"

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client

    def _key(self, user_id: str, conversation_id: str) -> str:
        return self.KEY_FMT.format(user_id=user_id, conversation_id=conversation_id)

    def _meta(self, user_id: str, conversation_id: str) -> str:
        return self.META_FMT.format(user_id=user_id, conversation_id=conversation_id)

    # ─── Read ────────────────────────────────────────────────────────

    async def history(
        self, user_id: str, conversation_id: str, *, max_turns: int = MAX_TURNS,
    ) -> list[Turn]:
        """Oldest → newest ordering."""
        r = self._redis or get_redis()
        raw = await r.lrange(self._key(user_id, conversation_id), 0, max_turns - 1)
        # List is stored newest-first via LPUSH; reverse to oldest-first for prompt.
        out: list[Turn] = []
        for blob in reversed(raw):
            try:
                d = json.loads(blob)
                out.append(Turn(
                    ts=float(d["ts"]),
                    step=int(d["step"]),
                    tool=str(d["tool"]),
                    args_summary=str(d.get("args_summary", "")),
                    outcome=str(d.get("outcome", "")),
                    reasoning=d.get("reasoning"),
                ))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
        return out

    async def goal(self, user_id: str, conversation_id: str) -> str | None:
        """Get the user's stated goal for this conversation."""
        r = self._redis or get_redis()
        return await r.hget(self._meta(user_id, conversation_id), "goal")

    # ─── Write ───────────────────────────────────────────────────────

    async def set_goal(
        self, user_id: str, conversation_id: str, goal: str,
    ) -> None:
        """Set / update the goal for this conversation."""
        r = self._redis or get_redis()
        meta_key = self._meta(user_id, conversation_id)
        pipe = r.pipeline()
        pipe.hset(meta_key, mapping={"goal": goal, "started_at": str(time.time())})
        pipe.expire(meta_key, self.TTL_SECONDS)
        await pipe.execute()

    async def append_turn(
        self, user_id: str, conversation_id: str, turn: Turn,
    ) -> None:
        r = self._redis or get_redis()
        key = self._key(user_id, conversation_id)
        blob = json.dumps(asdict(turn))
        pipe = r.pipeline()
        pipe.lpush(key, blob)
        pipe.ltrim(key, 0, self.MAX_TURNS - 1)
        pipe.expire(key, self.TTL_SECONDS)
        pipe.expire(self._meta(user_id, conversation_id), self.TTL_SECONDS)
        await pipe.execute()

    # ─── Housekeeping ────────────────────────────────────────────────

    async def clear(self, user_id: str, conversation_id: str) -> None:
        r = self._redis or get_redis()
        pipe = r.pipeline()
        pipe.delete(self._key(user_id, conversation_id))
        pipe.delete(self._meta(user_id, conversation_id))
        pipe.delete(self._plan_key(user_id, conversation_id))
        pipe.delete(self._draft_key(user_id, conversation_id))
        await pipe.execute()

    # ─── Plan persistence (plan-first execution) ─────────────────────

    PLAN_FMT = "convmem_plan:{user_id}:{conversation_id}"

    def _plan_key(self, user_id: str, conversation_id: str) -> str:
        return self.PLAN_FMT.format(user_id=user_id, conversation_id=conversation_id)

    async def set_plan(
        self, user_id: str, conversation_id: str, plan_dict: dict[str, Any],
    ) -> None:
        """Persist a plan for the session (survives agent-step reboots)."""
        r = self._redis or get_redis()
        await r.set(
            self._plan_key(user_id, conversation_id),
            json.dumps(plan_dict),
            ex=self.TTL_SECONDS,
        )

    async def get_plan(
        self, user_id: str, conversation_id: str,
    ) -> dict[str, Any] | None:
        r = self._redis or get_redis()
        raw = await r.get(self._plan_key(user_id, conversation_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ─── Pending reply draft (Round 4 — voice reply confirmation) ───
    # A single draft per conversation. The user says "reply …" → agent
    # calls stage_reply which stores here; next turn the agent emits
    # reply_to_notification using the values we hand back. If the user
    # abandons or replaces the draft, we just overwrite — there's only
    # one pending reply at a time per conversation.

    DRAFT_FMT = "convmem_draft:{user_id}:{conversation_id}"

    def _draft_key(self, user_id: str, conversation_id: str) -> str:
        return self.DRAFT_FMT.format(user_id=user_id, conversation_id=conversation_id)

    async def set_draft_reply(
        self, user_id: str, conversation_id: str,
        *,
        notification_key: str, contact: str, text: str,
    ) -> None:
        r = self._redis or get_redis()
        await r.set(
            self._draft_key(user_id, conversation_id),
            json.dumps({
                "notification_key": notification_key,
                "contact": contact,
                "text": text,
                "ts": time.time(),
            }),
            ex=self.TTL_SECONDS,
        )

    async def get_draft_reply(
        self, user_id: str, conversation_id: str,
    ) -> dict[str, Any] | None:
        r = self._redis or get_redis()
        raw = await r.get(self._draft_key(user_id, conversation_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def clear_draft_reply(
        self, user_id: str, conversation_id: str,
    ) -> None:
        r = self._redis or get_redis()
        await r.delete(self._draft_key(user_id, conversation_id))

    async def active_count(self, user_id: str) -> int:
        """Rough count of active conversations for a user (Redis scan)."""
        r = self._redis or get_redis()
        cursor = 0
        count = 0
        pattern = self.KEY_FMT.format(user_id=user_id, conversation_id="*")
        while True:
            cursor, keys = await r.scan(cursor=cursor, match=pattern, count=100)
            count += len(keys)
            if cursor == 0:
                break
        return count


# ─── Rendering to Claude prompt ──────────────────────────────────────


def format_for_prompt(turns: list[Turn], max_chars: int = 3_500) -> str:
    """Render turns as a compact history block suitable for Claude's context.

    Starts from the most recent turn and works backward so the budget is
    spent on the most relevant context. Output is oldest → newest to keep
    narrative order.
    """
    if not turns:
        return "(no prior turns in this conversation)"

    # Take most recent turns that fit budget, reverse back to chrono.
    acc: list[str] = []
    used = 0
    for t in reversed(turns):
        line = f"[{t.step}] {t.tool}({t.args_summary}) → {t.outcome}"
        if t.reasoning:
            line += f"  // {t.reasoning}"
        size = len(line) + 1
        if used + size > max_chars and acc:
            break
        acc.append(line)
        used += size
    acc.reverse()
    return "\n".join(acc)


# ─── Singleton ───────────────────────────────────────────────────────


_memory: ConversationMemory | None = None


def get_conversation_memory() -> ConversationMemory:
    global _memory
    if _memory is None:
        _memory = ConversationMemory()
    return _memory


# ─── Helpers callers will want ───────────────────────────────────────


def abbreviate_tool_args(tool: str, args: dict[str, Any]) -> str:
    """Make a single short string describing what a tool call did."""
    parts: list[str] = []
    if tool in ("tap_node", "type_in_node", "long_press_node"):
        i = args.get("node_index")
        if i is not None:
            parts.append(f"node={i}")
    if tool in ("tap", "long_press") and args.get("x") is not None:
        parts.append(f"at=({args['x']},{args['y']})")
    if tool in ("type", "type_in_node"):
        text = args.get("text") or ""
        parts.append(f'text="{text[:40]}{"…" if len(text) > 40 else ""}"')
    if tool == "open_app":
        parts.append(f"app={args.get('app_package', '')}")
    if tool in ("speak", "ask_user", "done", "give_up"):
        msg = (args.get("message") or "")[:60]
        parts.append(f'"{msg}"')
    if tool == "swipe":
        parts.append(f"dir={args.get('direction')}")
    return ", ".join(parts)
