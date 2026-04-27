"""Per-contact tone profile for outgoing replies.

Goal: when Sahay drafts a WhatsApp reply for the user, the wording
must sound like THE USER would write it to THIS PERSON. Generic
draft tone is the single biggest "Sahay wrote this" tell — and it's
the one the user has the strongest reaction to.

Two sources for the tone descriptor, in order:

  1. Explicit user memory. If the user has a `user_memory` row for
     this contact whose value contains a tone hint ("formal", "casual",
     "Tamil-English", "short", …) we extract those tokens.

  2. Category default. The same memory row tells us how the contact
     relates to the user (people/work, boss, family, friend). We map
     the relationship to a sane default tone — boss → formal,
     family/friend → casual, colleague → professional-friendly.

  3. Final fallback: "neutral, short, in the user's natural voice."

The profile is rendered as a 1-line descriptor that goes straight
into the reply critic's Haiku prompt. Cheap to compute (single SQL
query + dict lookups). Cached per (user, contact) for the lifetime
of one stage_reply turn — no need to persist beyond that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import UserMemory
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class ToneProfile:
    descriptor: str         # 1-line, fed straight into the critic prompt
    source: str             # "explicit", "category_default", "fallback"
    contact: str            # canonical contact name as we resolved it


# Tone hints we look for inside the contact memory's `value` field. Order
# is significant — earlier matches win when multiple are present, since
# they're stronger signals.
_TONE_KEYWORDS: list[str] = [
    "formal", "casual", "playful", "warm", "professional",
    "tamil-english", "hindi-english", "code-mixed",
    "short", "concise", "long", "detailed",
    "no greetings", "with greetings", "emojis", "no emojis",
]

# Default tone descriptors per relationship hint. Keys are normalised
# substrings we look for in the memory value (case-insensitive).
_RELATIONSHIP_DEFAULTS: dict[str, str] = {
    "boss":         "formal English, full sentences, sign-off optional, no emojis.",
    "manager":      "formal English, full sentences, sign-off optional, no emojis.",
    "client":       "formal English, polished, no contractions, no emojis.",
    "colleague":    "professional but friendly, short, full sentences, occasional contractions.",
    "coworker":     "professional but friendly, short, full sentences, occasional contractions.",
    "wife":         "casual, warm, short, contractions, emojis welcome.",
    "husband":      "casual, warm, short, contractions, emojis welcome.",
    "spouse":       "casual, warm, short, contractions, emojis welcome.",
    "girlfriend":   "casual, warm, short, contractions, emojis welcome.",
    "boyfriend":    "casual, warm, short, contractions, emojis welcome.",
    "partner":      "casual, warm, short, contractions, emojis welcome.",
    "amma":         "very warm, casual, short, code-mixed Tamil/Hindi-English, no formality.",
    "appa":         "very warm, casual, short, code-mixed Tamil/Hindi-English, no formality.",
    "mom":          "very warm, casual, short, code-mixed where natural.",
    "dad":          "very warm, casual, short, code-mixed where natural.",
    "brother":      "casual, blunt, short, contractions, emojis fine.",
    "sister":       "casual, warm, short, contractions, emojis fine.",
    "friend":       "casual, breezy, short, contractions and emojis fine.",
    "family":       "warm, casual, short.",
}

_FALLBACK_DESCRIPTOR = (
    "neutral and short, in the user's natural voice. "
    "No greetings or sign-offs unless the user typically uses them."
)


async def tone_for_contact(
    db: AsyncSession,
    *,
    user_id: UUID,
    contact: str,
) -> ToneProfile:
    """Resolve the tone descriptor for one (user, contact) pair.

    Tolerant lookup — matches on `key` exact (`contact:rahul`),
    `key` substring (handles "Rahul (work)" / "Rahul Kumar"), and
    finally `value` substring as last resort.
    """
    name = (contact or "").strip()
    if not name:
        return ToneProfile(_FALLBACK_DESCRIPTOR, "fallback", "")

    canonical = name.lower()
    try:
        stmt = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id)
            .where(UserMemory.category == "people")
        )
        rows = (await db.execute(stmt)).scalars().all()
    except Exception as e:
        log.debug("tone_lookup_failed", err=str(e))
        return ToneProfile(_FALLBACK_DESCRIPTOR, "fallback", name)

    matched: UserMemory | None = None
    for r in rows:
        key = (r.key or "").lower()
        if key == f"contact:{canonical}" or key.endswith(f":{canonical}"):
            matched = r
            break
        # Substring match on key after stripping the prefix.
        bare = key.removeprefix("contact:")
        if canonical in bare or bare in canonical:
            matched = r
            break

    if matched is None:
        # Last resort: any memory whose value mentions this name.
        for r in rows:
            if canonical in (r.value or "").lower():
                matched = r
                break

    if matched is None:
        return ToneProfile(_FALLBACK_DESCRIPTOR, "fallback", name)

    value_l = (matched.value or "").lower()

    # 1. Explicit tone keywords in the memory value.
    explicit_hits = [kw for kw in _TONE_KEYWORDS if kw in value_l]
    if explicit_hits:
        descriptor = (
            f"{', '.join(explicit_hits[:5])}. "
            "Match the user's voice for this contact specifically."
        )
        return ToneProfile(descriptor, "explicit", name)

    # 2. Relationship-default tone.
    for rel, default in _RELATIONSHIP_DEFAULTS.items():
        # Match whole-word boundaries to avoid "client" matching "clientele"
        # or "boss" matching "bossanova".
        if re.search(rf"\b{re.escape(rel)}\b", value_l):
            return ToneProfile(default, "category_default", name)

    # 3. Final fallback.
    return ToneProfile(_FALLBACK_DESCRIPTOR, "fallback", name)
