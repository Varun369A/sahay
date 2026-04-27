"""Cheap Haiku pass that cleans a raw STT transcript before dispatch.

Why this exists: Sarvam (or any STT) reliably trips on Indian names,
code-mixed Tamil/Hindi-English, and informal phrasing. The raw
transcript "send rahool a msg saying I'll be there in 10" is what
the agent loop sees today. Even with a smart agent, that ambiguity
gets compounded across turns.

This module routes the raw transcript through Haiku 4.5 with the
user's top contacts as normalization context. The output is the
cleaned canonical goal + a confidence score. Cost ~₹0.03 per
rewrite — negligible compared to the cost of a wasted multi-turn
agent run on a misheard goal.

Wired into `agent_engine.step()` at `step_index == 0` only — first
turn carries the user's spoken intent; subsequent turns are device
state observations and don't need rewriting.

Refusal envelope: if Haiku returns invalid JSON or a refusal, we
fall back to the original text. The rewriter must NEVER block the
goal from reaching the agent loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from sahay.db.models import UserMemory
from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

log = get_logger(__name__)


# Below this confidence Haiku reports, we surface the rewrite as a
# "did you mean…" hint to the device but still proceed with the
# rewritten text. Wire format only — no behaviour change in the agent
# loop today; future task can wire the device-side confirm flow.
LOW_CONFIDENCE_THRESHOLD = 0.65


@dataclass(slots=True)
class RewriteResult:
    rewritten: str
    confidence: float
    was_changed: bool
    original: str
    cost_inr_paise: int = 0


_SYSTEM = """You are Sahay's Voice Goal Rewriter. You receive a raw speech-to-text transcript and rewrite it into a clean, canonical command an agent can act on.

Hard rules:
1. Output ONLY valid JSON. No prose, no markdown fences. Schema:
   {"goal": "<rewritten command>", "confidence": <0.0-1.0>}
2. Preserve the user's intent. NEVER change what they want — only how it's phrased.
3. Fix obvious STT errors: typos, wrong phonetic guesses for Indian names, missing punctuation.
4. Normalize contact references against the CONTACTS list when one is provided. ("rahool" → "Rahul" if Rahul is in the list.)
5. Keep code-mixed Tamil/Hindi-English where it appears; do not translate.
6. If the transcript is already clean, RETURN IT UNCHANGED with confidence=1.0.
7. If the transcript is too garbled to interpret confidently, return the closest plausible reading with confidence < 0.5.
"""


async def rewrite_goal(
    db: AsyncSession | None,
    *,
    user_id: UUID | None,
    raw_text: str,
) -> RewriteResult:
    """Rewrite a raw STT transcript into a canonical goal.

    Falls back gracefully on any error: the agent loop must never
    crash because the rewriter had a bad day.
    """
    raw = (raw_text or "").strip()
    if not raw:
        return RewriteResult(rewritten=raw, confidence=0.0, was_changed=False, original=raw)

    # Skip the LLM call entirely for trivially-short or obviously-clean
    # inputs — saves ~₹0.03 per skip.
    if _looks_clean(raw):
        return RewriteResult(rewritten=raw, confidence=1.0, was_changed=False, original=raw)

    contacts_block = await _contacts_block(db, user_id)
    user_msg = f"CONTACTS:\n{contacts_block}\n\nTRANSCRIPT:\n{raw}\n"

    try:
        result = await complete(
            workload="default",                          # Haiku 4.5
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=256,
            temperature=0.0,
        )
    except Exception as e:
        log.warning("goal_rewrite_failed_falling_back", err=str(e))
        return RewriteResult(rewritten=raw, confidence=0.0, was_changed=False, original=raw)

    parsed = _parse_response(result.content)
    if parsed is None:
        log.info("goal_rewrite_unparseable_falling_back", raw=raw[:80])
        return RewriteResult(
            rewritten=raw, confidence=0.0, was_changed=False, original=raw,
            cost_inr_paise=result.cost_inr_paise,
        )

    rewritten, confidence = parsed
    return RewriteResult(
        rewritten=rewritten,
        confidence=confidence,
        was_changed=(rewritten.strip() != raw.strip()),
        original=raw,
        cost_inr_paise=result.cost_inr_paise,
    )


# ─── Helpers ────────────────────────────────────────────────────────


# Threshold for "skip the LLM, this looks fine." Conservative — better
# to spend ~₹0.03 on a clean rewrite than to ship a typo to the agent.
_CLEAN_MAX_LEN = 12     # chars
_CLEAN_RE = re.compile(r"^[a-zA-Z0-9 ,.!?'-]+$")


def _looks_clean(raw: str) -> bool:
    """Heuristic — short ASCII-only utterances are most likely already
    clean. The rewriter still runs on anything longer or non-ASCII
    (the typical Indian-English / code-mixed case)."""
    if len(raw) > _CLEAN_MAX_LEN:
        return False
    return bool(_CLEAN_RE.fullmatch(raw))


async def _contacts_block(
    db: AsyncSession | None,
    user_id: UUID | None,
) -> str:
    """Top-N contact memories rendered as 'name → details' lines for
    Haiku to normalize against. Empty when the DB or user is missing —
    the rewrite still works, just without contact normalization."""
    if db is None or user_id is None:
        return "(none)"
    try:
        stmt = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id)
            .where(UserMemory.category == "people")
            .order_by(UserMemory.importance.desc(), UserMemory.last_accessed_at.desc())
            .limit(20)
        )
        rows = (await db.execute(stmt)).scalars().all()
    except Exception as e:
        log.debug("contacts_block_load_failed", err=str(e))
        return "(none)"
    if not rows:
        return "(none)"
    lines: list[str] = []
    for m in rows:
        # `key` for contacts looks like "contact:rahul" — strip the prefix
        # so Haiku sees just the name.
        name = (m.key or "").removeprefix("contact:").strip() or m.key
        lines.append(f"  • {name}: {m.value[:80]}")
    return "\n".join(lines) or "(none)"


def _parse_response(raw: str) -> tuple[str, float] | None:
    """Tolerant parser — strips fences, validates the two fields we need.
    Returns None if the response is unusable."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?", "", s).strip()
        s = re.sub(r"```$", "", s).strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    goal = data.get("goal")
    confidence = data.get("confidence")
    if not isinstance(goal, str) or not goal.strip():
        return None
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        return None
    conf = max(0.0, min(1.0, conf))
    return goal.strip(), conf
