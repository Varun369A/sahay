"""Voice-utterance → structured intent dispatcher.

Given any free-form utterance ("play dune on netflix" / "ப்ளே துரந்தர் ஆன் நெட்பிளிக்ஸ்"),
asks Claude Haiku to pick the best matching playbook and extract inputs.

Used by /v1/actions/dispatch. Cheap (Haiku), fast (~500ms), and language-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook
from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class DispatchResult:
    matched: bool
    app_package: str | None = None
    intent: str | None = None
    inputs: dict[str, Any] | None = None
    confidence: float = 0.0
    notes: str | None = None
    cost_inr_paise: int = 0


_SYSTEM_TEMPLATE = """You are Sahay's voice-intent dispatcher.

You receive a user utterance (in any language — English, Tamil, Hindi, Tanglish, code-switched) and a list of available playbooks. Pick the best matching playbook and extract the input parameters.

AVAILABLE PLAYBOOKS:
{playbooks}

Hard rules:
1. Output ONLY valid JSON: {{"matched": bool, "app_package": str, "intent": str, "inputs": {{...}}, "confidence": 0.0-1.0, "notes": str}}
2. If no playbook matches the user's intent (or you're unsure), return {{"matched": false, "confidence": 0.0, "notes": "<why>"}}.
3. NEVER invent app_package or intent names. They MUST come from the list above.
4. Translate transcribed Indic-script text to its underlying meaning. e.g. "ப்ளே துரந்தர்" means "play dune".
5. Extract every input parameter the playbook declares. If you can't determine an input, leave its value as an empty string.
6. confidence < 0.7 means the user should be asked to clarify; >= 0.7 means proceed.

Examples:

Utterance: "play dune on netflix"
Output: {{"matched": true, "app_package": "com.netflix.mediaclient", "intent": "search_and_play", "inputs": {{"title": "dune"}}, "confidence": 0.95, "notes": ""}}

Utterance: "send amirtha a whatsapp message saying hi"
Output: {{"matched": true, "app_package": "com.whatsapp", "intent": "send_message", "inputs": {{"recipient_name": "amirtha", "message_text": "hi"}}, "confidence": 0.92, "notes": ""}}

Utterance: "என்ன செய்ய முடியும்" (= "what can you do")
Output: {{"matched": false, "confidence": 0.0, "notes": "user is asking capability, not requesting action"}}
"""


_USER_TEMPLATE = "Utterance: \"{utterance}\"\nLocale: {locale}\n\nReturn JSON only."


async def dispatch(
    db: AsyncSession,
    utterance: str,
    locale: str = "en",
) -> DispatchResult:
    if not utterance or not utterance.strip():
        return DispatchResult(matched=False, notes="empty_utterance")

    playbooks = await _list_playbooks_for_prompt(db)
    if not playbooks:
        return DispatchResult(matched=False, notes="no_playbooks_available")

    system = _SYSTEM_TEMPLATE.format(playbooks=playbooks)
    user_msg = _USER_TEMPLATE.format(utterance=utterance.strip(), locale=locale)

    try:
        result = await complete(
            workload="default",                  # Haiku — cheap + fast
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=512,
            temperature=0.0,
        )
    except Exception as e:
        log.exception("dispatch_claude_error")
        return DispatchResult(matched=False, notes=f"claude_error:{e!s}")

    raw = result.content.strip()
    if raw.startswith("```"):
        raw = raw.lstrip("`").lstrip("json").strip().rstrip("`").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("dispatch_invalid_json", raw=raw[:200])
        return DispatchResult(
            matched=False, notes="invalid_json", cost_inr_paise=result.cost_inr_paise,
        )

    if not isinstance(data, dict) or not data.get("matched"):
        return DispatchResult(
            matched=False,
            confidence=float(data.get("confidence", 0.0)),
            notes=str(data.get("notes", "no_match")),
            cost_inr_paise=result.cost_inr_paise,
        )

    return DispatchResult(
        matched=True,
        app_package=str(data.get("app_package", "")),
        intent=str(data.get("intent", "")),
        inputs=dict(data.get("inputs") or {}),
        confidence=float(data.get("confidence", 0.0)),
        notes=str(data.get("notes", "")),
        cost_inr_paise=result.cost_inr_paise,
    )


async def _list_playbooks_for_prompt(db: AsyncSession) -> str:
    """Build a compact text catalog of playbooks for the system prompt.

    Cached implicitly via Anthropic prompt caching (system prompt is stable).
    """
    stmt = select(
        Playbook.app_package,
        Playbook.app_name,
        Playbook.intent,
        Playbook.steps_json,
    ).where(Playbook.is_payment_app.is_(False))
    rows = (await db.execute(stmt)).all()

    lines: list[str] = []
    for pkg, name, intent, steps in rows:
        inputs = (steps or {}).get("inputs") or []
        inputs_str = ", ".join(inputs) if inputs else "(none)"
        lines.append(f"- {pkg} :: {intent}  (app: {name}, inputs: {inputs_str})")
    return "\n".join(lines)
