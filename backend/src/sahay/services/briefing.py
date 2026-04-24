"""Morning briefing composer.

When the user first unlocks their phone in the morning, Sahay speaks a
short, calm summary: overnight messages, missed calls, open follow-ups,
the day's weather. One cheap Sonnet call stitches it together from the
raw inputs — signals Sahay has been ambient, not scripted.

Inputs gathered (all best-effort, any can fail silently):
  • Recent notifications (last 10h) — from NotificationStore Redis.
  • Missed-call count — derived from notifications with CATEGORY_MISSED_CALL
    or the title pattern common to Android dialers.
  • Open follow-ups — from FollowUpStore.
  • Long-term memory top-K — gives Sahay continuity ("yesterday you said…").
  • User role + locale — drives tone + weather localisation.
  • Web search (Anthropic server-side) — included in the Sonnet request
    so weather / news current at call time lands in the briefing when
    the model decides it needs them.

Output: a single natural-language sentence, ≤ 220 chars, TTS-ready.
Never contains PII tokens or URLs; the Sonnet prompt enforces that.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sahay.services import memory_service
from sahay.services.claude_client import _PRICING_PAISE_PER_MTOK, _get_client
from sahay.services.follow_ups import (
    format_for_prompt as format_follow_ups,
    get_follow_up_store,
)
from sahay.services.notification_store import (
    Notification, get_notification_store,
)
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Briefing:
    text: str
    model: str
    tokens_used: int
    cost_inr_paise: int
    web_searches: int


# ─── Missed-call heuristic ───────────────────────────────────────────
# Android dialers post missed calls with category "missed_call" OR an
# empty category but a title that starts with "Missed call". We count
# by package + title pattern.


_MISSED_CALL_CATEGORIES = {"missed_call", "call"}
_MISSED_CALL_TITLE_PREFIXES = ("missed call", "मिस्ड कॉल", "তপথ কল")


def _count_missed_calls(notifs: list[Notification]) -> int:
    count = 0
    for n in notifs:
        if n.category.lower() in _MISSED_CALL_CATEGORIES:
            count += 1
            continue
        title_lower = n.title.lower()
        if any(title_lower.startswith(p) for p in _MISSED_CALL_TITLE_PREFIXES):
            count += 1
    return count


# ─── Briefing prompt ─────────────────────────────────────────────────


_BRIEFING_SYSTEM = """You are the morning-briefing composer for {name} — an Android personal agent. You produce one short spoken sentence the user hears as soon as they unlock their phone for the day. {role_block}

Output rules:
  • Single sentence, ≤ 220 characters. TTS-friendly — short clauses separated by commas.
  • Start with "Good morning" OR "Good {time_of_day}" (use {time_of_day}). Nothing else as opener.
  • Prioritise ruthlessly: one or two items, not a news desk. If nothing is worth mentioning, say "Good morning. Nothing urgent." and stop.
  • Mention: overnight messages (with sender name if you can identify one from notifications), missed calls (count), open follow-ups (if urgent), today's weather if the web reveals it for the user's region.
  • NEVER read out a URL, a phone number, an email address, or any `<TOKEN_N>` placeholder.
  • NEVER speculate about health, legal, or financial topics beyond the day's general weather.
  • Natural Indian-English phrasing unless the user's role makes another style obvious. "Two WhatsApps" > "two WhatsApp notifications".
  • Return ONLY the sentence. No preamble, no markdown, no quotes.
"""


def _time_of_day(now: float) -> str:
    hour = int((now // 3600) % 24)
    if hour < 4: return "night"
    if hour < 12: return "morning"
    if hour < 17: return "afternoon"
    return "evening"


def _compact_notifications_for_briefing(notifs: list[Notification]) -> str:
    if not notifs:
        return "(none)"
    lines: list[str] = []
    for n in notifs[:12]:
        app = n.app_name or n.package.split(".")[-1]
        title = n.title or "(no title)"
        text = n.text.replace("\n", " ")[:120]
        lines.append(f"  • [{app}] {title}" + (f" — \"{text}\"" if text else ""))
    return "\n".join(lines)


async def compose(
    *,
    db: AsyncSession | None,
    user_id: str,
    assistant_name: str,
    role: str | None,
    locale: str | None,
) -> Briefing:
    """Build and speak back a single short morning-briefing sentence."""
    now = time.time()
    tod = _time_of_day(now)

    # ── Gather ingredients ──
    notif_store = get_notification_store()
    fu_store = get_follow_up_store()

    try:
        recent = await notif_store.recent(user_id, limit=40, since_seconds=10 * 3600)
    except Exception as e:                                   # noqa: BLE001
        log.warning("briefing_notif_fail", err=str(e))
        recent = []

    try:
        follow_ups = await fu_store.list(user_id, limit=6)
    except Exception as e:                                   # noqa: BLE001
        log.warning("briefing_followups_fail", err=str(e))
        follow_ups = []

    ltm_block = "(no stored memories)"
    if db is not None:
        try:
            mems = await memory_service.top_for_prompt(
                db, user_id=UUID(user_id),
            )
            ltm_block = memory_service.format_for_prompt(mems)
        except Exception as e:                               # noqa: BLE001
            log.warning("briefing_ltm_fail", err=str(e))

    missed_calls = _count_missed_calls(recent)
    non_call_notifs = [
        n for n in recent
        if n.category.lower() not in _MISSED_CALL_CATEGORIES
        and not any(n.title.lower().startswith(p) for p in _MISSED_CALL_TITLE_PREFIXES)
    ]

    user_text = (
        f"OVERNIGHT NOTIFICATIONS (last 10h, total {len(recent)}):\n"
        f"{_compact_notifications_for_briefing(non_call_notifs)}\n\n"
        f"MISSED CALLS: {missed_calls}\n\n"
        f"{format_follow_ups(follow_ups)}\n\n"
        f"WHAT YOU KNOW ABOUT THIS USER:\n{ltm_block}\n\n"
        "If the user's locale is *-IN or Indian, you may search for "
        "today's weather in their region before composing (optional, "
        "only if it feels briefing-worthy)."
    )

    # ── Role block reuses the agent's persona fragments for consistency
    from sahay.services.agent_engine import _ROLE_BLOCKS
    role_block = _ROLE_BLOCKS.get((role or "other").lower(), _ROLE_BLOCKS["other"])

    # ── Localised web search (same pattern the agent uses)
    from sahay.services.agent_engine import _web_search_tool_for

    system = _BRIEFING_SYSTEM.format(
        name=assistant_name, role_block=role_block, time_of_day=tod,
    )

    client = _get_client()
    try:
        # Sonnet 4.6 — we want prose quality, not Haiku speed.
        msg = await client.messages.create(
            model="claude-sonnet-4-6",
            system=[{
                "type": "text", "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_text}],
            max_tokens=300,
            temperature=0.4,
            tools=[_web_search_tool_for(locale)],
        )
    except Exception as e:                                   # noqa: BLE001
        log.exception("briefing_sonnet_failed")
        # Deterministic fallback so the user still hears *something*.
        parts = [f"Good {tod}."]
        if recent:
            parts.append(f"{len(recent)} overnight notifications.")
        if missed_calls:
            parts.append(f"{missed_calls} missed call{'s' if missed_calls > 1 else ''}.")
        if follow_ups:
            parts.append(f"{len(follow_ups)} open follow-up{'s' if len(follow_ups) > 1 else ''}.")
        return Briefing(
            text=" ".join(parts)[:220],
            model="fallback",
            tokens_used=0,
            cost_inr_paise=0,
            web_searches=0,
        )

    # Collect final text (concatenate all text blocks).
    text_out = "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    ).strip()
    text_out = text_out.replace("\n", " ").strip()[:220]
    if not text_out:
        text_out = f"Good {tod}."

    # Pricing + web-search tally
    usage = msg.usage
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    pricing = _PRICING_PAISE_PER_MTOK.get(
        msg.model, _PRICING_PAISE_PER_MTOK["claude-haiku-4-5"]
    )
    fresh_in = max(0, usage.input_tokens - cached)
    token_paise = (
        fresh_in * pricing["input"]
        + cached * pricing["cached_input"]
        + usage.output_tokens * pricing["output"]
    ) // 1_000_000
    server_tool = getattr(usage, "server_tool_use", None)
    web_calls = (
        getattr(server_tool, "web_search_requests", 0) or 0
        if server_tool is not None else 0
    )
    # ≈ ₹0.85 per web search (same as agent_engine)
    cost_paise = token_paise + web_calls * 85

    log.info(
        "briefing_composed",
        chars=len(text_out), model=msg.model, cost_paise=cost_paise,
        web_searches=web_calls, notifs=len(recent), missed=missed_calls,
        follow_ups=len(follow_ups),
    )
    return Briefing(
        text=text_out,
        model=msg.model,
        tokens_used=usage.input_tokens + usage.output_tokens,
        cost_inr_paise=cost_paise,
        web_searches=web_calls,
    )
