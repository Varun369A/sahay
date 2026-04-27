"""Tone-aware reply critic.

Pairs with `tone_profiler` to make staged replies sound like the user.
Given a draft message + a per-contact tone descriptor, asks Haiku 4.5
to rewrite the draft to match that tone — or pass it through unchanged
if it's already on tone.

Cost shape: one Haiku call per `stage_reply` invocation. Currently
the agent only calls stage_reply when the user asks to send a message,
so this is a low-volume hot path. ~₹0.04 per critique.

Refusal envelope: any error or unparseable response falls back to the
original draft. The reply critic is a quality-of-life improvement —
it must NEVER block a reply from being staged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class CritiqueResult:
    text: str               # final text the device should use
    was_rewritten: bool
    reason: str             # short note for logs / dashboard
    cost_inr_paise: int = 0


_SYSTEM = """You are Sahay's Reply Critic. You receive:
  • a TONE descriptor describing how the user typically writes to this contact
  • a DRAFT reply written by the agent

Your job: rewrite the DRAFT so it sounds like the user wrote it to THIS contact. Generic agent tone is the single biggest "Sahay wrote this" tell — kill it.

Hard rules:
1. Output ONLY valid JSON. Schema:
   {"text": "<final reply>", "rewritten": <bool>, "reason": "<short note>"}
2. Preserve the user's intent. NEVER change what the message is about — only how it's phrased.
3. If the draft already matches the tone, return rewritten=false and `text` unchanged.
4. NEVER add a "best regards / cheers / -Sahay" sign-off the user wouldn't naturally use.
5. NEVER add greetings ("hi/hello/hey") unless the tone descriptor says they do.
6. Strip filler that screams agent-prose: "Just wanted to let you know", "Hope this helps", "Please let me know if you have any questions".
7. Keep emojis ONLY if the tone descriptor allows them.
8. Match the language register — code-mixed Tamil/Hindi-English stays code-mixed.
9. Output `reason` in 8 words or fewer (e.g. "removed corporate filler", "added casual contraction", "ok as-is").
"""


async def critique_draft(
    *,
    draft: str,
    tone_descriptor: str,
    contact: str,
) -> CritiqueResult:
    """Run the critic over one draft. Always returns a usable text —
    falls back to the original on any failure path."""
    draft = (draft or "").strip()
    if not draft:
        return CritiqueResult(text=draft, was_rewritten=False, reason="empty_draft")

    user_msg = (
        f"CONTACT: {contact or 'unknown'}\n"
        f"TONE: {tone_descriptor}\n"
        f"DRAFT:\n{draft}\n"
    )
    try:
        result = await complete(
            workload="default",                        # Haiku 4.5
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=384,
            temperature=0.0,
        )
    except Exception as e:
        log.warning("reply_critic_call_failed", err=str(e))
        return CritiqueResult(text=draft, was_rewritten=False, reason="call_failed")

    parsed = _parse_response(result.content)
    if parsed is None:
        log.info("reply_critic_unparseable", raw=result.content[:120])
        return CritiqueResult(
            text=draft, was_rewritten=False, reason="unparseable",
            cost_inr_paise=result.cost_inr_paise,
        )

    new_text, was_rewritten, reason = parsed
    # Defensive: refuse to silently drop the message body.
    if not new_text.strip():
        return CritiqueResult(
            text=draft, was_rewritten=False, reason="critic_blanked",
            cost_inr_paise=result.cost_inr_paise,
        )
    return CritiqueResult(
        text=new_text,
        was_rewritten=was_rewritten,
        reason=reason or ("rewritten" if was_rewritten else "ok_as_is"),
        cost_inr_paise=result.cost_inr_paise,
    )


def _parse_response(raw: str) -> tuple[str, bool, str] | None:
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
    text = data.get("text")
    rewritten = data.get("rewritten")
    reason = data.get("reason", "")
    if not isinstance(text, str):
        return None
    if not isinstance(rewritten, bool):
        # Tolerate truthy strings — Haiku occasionally emits "true" / "false"
        if isinstance(rewritten, str):
            rewritten = rewritten.strip().lower() in ("true", "yes", "1")
        else:
            return None
    if not isinstance(reason, str):
        reason = ""
    return text.strip(), rewritten, reason.strip()[:80]
