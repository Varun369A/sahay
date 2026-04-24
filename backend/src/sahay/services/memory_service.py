"""Long-term memory service.

Purpose
───────
Persistent facts about the user that survive across conversations.
Different from conversation_memory (in-Redis, per-session) — this is
the agent's knowledge of who the user is, what they prefer, who they
talk to, how they work.

Examples
────────
    key="boss-name"              value="Rakesh"
    key="preferred-voice-locale" value="ta-IN"
    key="contact:amirtha"        value="IT dept, calls with SIM 1, number <PHONE_12>"
    key="morning-routine"        value="check WhatsApp, skim news, reply to urgent emails"
    key="netflix-profile"        value="first profile, name starts with V"

API used by the agent
─────────────────────
Two tools are surfaced to Claude (see agent_engine.py):

    remember(key, value, category, importance)
        → upsert a fact. Idempotent by (user_id, key).

    forget(key)
        → delete a fact that's wrong / obsolete.

On every agent turn, the top-K most relevant memories are auto-injected
into the system prompt — Claude does not need to explicitly recall.

Relevance scoring
─────────────────
Score = importance * recency_decay
    recency_decay = exp(-hours_since_last_access / TAU_HOURS)
    TAU_HOURS = 240 (10 days) — after 10 days, weight halves.
    Minimum floor 0.1 so very old but important facts still show.

Budgeting
─────────
Inject at most MAX_INJECT_CHARS (~2500) worth of memories per turn.
The ranking ensures most-valuable facts fit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import UserMemory
from sahay.util.logging import get_logger

log = get_logger(__name__)


# ─── Tunables ────────────────────────────────────────────────────────

TAU_HOURS = 240.0               # ~10 days half-life for recency decay
MIN_DECAY = 0.10                # floor so old-but-important memories still rank
MAX_INJECT_CHARS = 2_500        # token budget for memory block in system prompt
MAX_TOP_K = 40                  # never load more than this at once
MAX_KEY_LEN = 128
MAX_VALUE_LEN = 2_000


# ─── Slugify keys so models reference them consistently ──────────────

_SLUG_RE = re.compile(r"[^a-z0-9:_-]+")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


def slugify_key(raw: str) -> str:
    """Normalise a memory key so models can't create dupes with different casing.

    Preserves `:` (for namespace-y keys like "contact:amirtha") and `_` / `-`.
    Everything else collapses into `-`.
    """
    s = raw.strip().lower()
    s = _SLUG_RE.sub("-", s)
    s = _MULTI_HYPHEN_RE.sub("-", s).strip("-")
    return s[:MAX_KEY_LEN] or "unnamed"


# ─── Public dataclass ────────────────────────────────────────────────


@dataclass(slots=True)
class Memory:
    key: str
    value: str
    category: str
    importance: int
    last_accessed_at: datetime
    source: str


# ─── CRUD ────────────────────────────────────────────────────────────


async def remember(
    db: AsyncSession,
    *,
    user_id: UUID,
    key: str,
    value: str,
    category: str = "facts",
    importance: int = 5,
    source: str = "agent",
) -> Memory:
    """Upsert a memory. Safe against the model calling this repeatedly.

    If the key exists, we update value + category + importance and bump
    updated_at. We INTENTIONALLY do NOT refresh last_accessed_at here —
    that's a read signal, not a write signal.
    """
    k = slugify_key(key)
    v = value.strip()[:MAX_VALUE_LEN]
    if not k or not v:
        raise ValueError("memory key and value must be non-empty")
    imp = max(1, min(10, importance))

    stmt = pg_insert(UserMemory).values(
        user_id=user_id,
        key=k,
        value=v,
        category=category[:32] or "facts",
        importance=imp,
        source=source[:16] or "agent",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "key"],
        set_={
            "value": v,
            "category": category[:32] or "facts",
            "importance": imp,
            "source": source[:16] or "agent",
            "updated_at": datetime.now(timezone.utc),
        },
    ).returning(UserMemory)

    row = (await db.execute(stmt)).scalar_one()
    log.info("memory_remember", user_id=str(user_id), key=k,
             category=category, importance=imp)
    return _to_dc(row)


async def forget(
    db: AsyncSession, *, user_id: UUID, key: str,
) -> bool:
    """Delete a memory. Returns True if something was deleted."""
    k = slugify_key(key)
    from sqlalchemy import delete as sqldelete
    res = await db.execute(
        sqldelete(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.key == k,
        )
    )
    deleted = (res.rowcount or 0) > 0
    if deleted:
        log.info("memory_forget", user_id=str(user_id), key=k)
    return deleted


async def list_all(
    db: AsyncSession, *, user_id: UUID, limit: int = 200,
) -> list[Memory]:
    stmt = (
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.importance.desc(), UserMemory.last_accessed_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dc(r) for r in rows]


async def list_important_contacts(
    db: AsyncSession, *, user_id: UUID, min_importance: int = 8, limit: int = 50,
) -> list[Memory]:
    """Contacts with importance ≥ threshold — fed to the Android listener so it
    can decide which notifications break through Quiet Hours.

    Pulls:
      • category='people' memories whose value/key mentions a person
      • any memory with importance >= min_importance and key prefix 'contact:'
    """
    stmt = (
        select(UserMemory)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.importance >= min_importance,
            UserMemory.category.in_(("people", "work")),
        )
        .order_by(UserMemory.importance.desc(), UserMemory.last_accessed_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dc(r) for r in rows]


# ─── Retrieval for agent system prompt ───────────────────────────────


async def top_for_prompt(
    db: AsyncSession, *, user_id: UUID, k: int = MAX_TOP_K,
) -> list[Memory]:
    """Return the top-k memories ranked by importance × recency decay.

    Also refreshes last_accessed_at for returned rows so the recency
    signal reflects "recently useful" not just "recently written".
    """
    k = max(1, min(k, MAX_TOP_K))
    stmt = (
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        # fetch more than we need so scoring can re-rank
        .order_by(UserMemory.importance.desc(), UserMemory.last_accessed_at.desc())
        .limit(k * 3)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return []

    now = datetime.now(timezone.utc)
    scored: list[tuple[float, UserMemory]] = []
    for r in rows:
        hours = max(
            0.0,
            (now - r.last_accessed_at.replace(tzinfo=timezone.utc if r.last_accessed_at.tzinfo is None else r.last_accessed_at.tzinfo)).total_seconds() / 3600.0,
        )
        decay = max(MIN_DECAY, math.exp(-hours / TAU_HOURS))
        score = r.importance * decay
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [r for _, r in scored[:k]]

    # Bump last_accessed_at for returned rows (don't await; one round-trip)
    if picked:
        ids = [r.id for r in picked]
        await db.execute(
            update(UserMemory)
            .where(UserMemory.id.in_(ids))
            .values(last_accessed_at=now)
        )

    return [_to_dc(r) for r in picked]


def format_for_prompt(memories: list[Memory], max_chars: int = MAX_INJECT_CHARS) -> str:
    """Render a compact block for Claude's system prompt."""
    if not memories:
        return "(no stored memories yet)"

    lines: list[str] = []
    used = 0
    for m in memories:
        line = f"- [{m.category}/{m.importance}] {m.key}: {m.value}"
        if used + len(line) + 1 > max_chars and lines:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


# ─── Internals ───────────────────────────────────────────────────────


def _to_dc(row: UserMemory) -> Memory:
    return Memory(
        key=row.key,
        value=row.value,
        category=row.category,
        importance=row.importance,
        last_accessed_at=row.last_accessed_at,
        source=row.source,
    )
