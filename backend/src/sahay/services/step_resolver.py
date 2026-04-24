"""Vision-grounded fallback step resolver.

When the on-device PlaybookExecutor can't find the target node using the
playbook's fixed selector (because the app changed UI, an account picker
appeared, etc.), it sends the current accessibility tree to this resolver.

Claude Haiku reads the tree and picks the right node. This is what lets
Sahay survive UI drift and conditional screens (e.g. Netflix profile
picker, WhatsApp 'Continue' splash, login walls).

Cost: ~₹0.1 per resolve call. Only fires on selector miss, so most
successful runs cost nothing extra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class ResolveResult:
    found: bool = False
    selector: dict[str, Any] = field(default_factory=dict)
    action: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    notes: str | None = None
    cost_inr_paise: int = 0


_SYSTEM = """You are Sahay's step resolver. Sahay is a fully-automated assistant — the user expects every task to complete end-to-end without human intervention. Your job: given a screen tree and a step description, pick a node to act on. ALWAYS try to find something; only return found=false when the screen is clearly unrelated to the task.

Rules:
1. Output ONLY valid JSON: {"found": bool, "selector": {...}, "action": str, "params": {...}, "confidence": 0.0-1.0, "notes": str}
2. selector keys: "viewId", "text", "contentDescription", "className" — pick whichever uniquely identifies the right node. Prefer viewId > text > contentDescription > className.
3. action must be one of: tap, long_press, type, swipe, wait, read, verify, back, home.
4. For 'type' actions, also include params.text — substitute any {{input}} placeholders from the inputs map.
5. Never invent viewIds. Use only IDs that appear in the provided tree.

AGGRESSIVE MATCHING — the user wants full automation. Bias toward found=true:
6. PROFILE PICKER: If you see multiple clickable tiles with names/avatars (e.g. Netflix "Who's watching?", Prime Video, Hotstar), pick THE FIRST clickable profile node. Don't ask. Users expect Sahay to pick their default profile automatically.
7. CONSENT / PERMISSION / CONTINUE DIALOGS: Tap OK / Continue / Accept / Allow / Got it / Maybe later / Not now (whichever advances past the dialog).
8. SEARCH-LIKE STEPS: If the description mentions "search" and there's ANY magnifier icon / text field / "Search" text, tap it.
9. RESULT TILES: If the step says "tap first result" or "tap matching title", pick the first clickable tile below the search bar.
10. If the screen has no obvious target for the step but IS the expected app: try the closest semantic match by description keywords. Return that with confidence 0.5+. Only return found=false when the current screen is a DIFFERENT APP entirely (like launcher or a hijacker).

Example:

Step: "Tap first result"  Action: tap  Inputs: {"title": "Dune"}
Tree: {"package":"com.netflix.mediaclient","nodes":[
  {"vid":"com.netflix.mediaclient:id/lolomo","cls":"RecyclerView","clickable":false},
  {"vid":"com.netflix.mediaclient:id/title_card","cls":"FrameLayout","cd":"Dune (2021)","clickable":true},
  {"vid":"com.netflix.mediaclient:id/title_card","cls":"FrameLayout","cd":"Dune: Part Two","clickable":true}
]}
Output: {"found": true, "selector": {"viewId":"com.netflix.mediaclient:id/title_card","contentDescription":"Dune (2021)"}, "action":"tap", "params":{}, "confidence":0.85, "notes":"first dune card"}
"""


_USER_TEMPLATE = """App: {app_package}
Step: "{step_description}"
Desired action: {desired_action}
Inputs: {inputs}

Current screen tree (JSON, truncated):
{tree}

Pick a node and return JSON.
"""


async def resolve(
    *,
    app_package: str,
    step_description: str,
    desired_action: str,
    screen_tree_summary: str,
    inputs: dict[str, Any] | None = None,
) -> ResolveResult:
    inputs = inputs or {}
    tree = (screen_tree_summary or "")[:18_000]

    user_msg = _USER_TEMPLATE.format(
        app_package=app_package,
        step_description=step_description,
        desired_action=desired_action,
        inputs=json.dumps(inputs),
        tree=tree,
    )

    try:
        result = await complete(
            workload="default",            # Haiku — cheap fallback
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=512,
            temperature=0.0,
        )
    except Exception as e:                  # noqa: BLE001
        log.exception("resolve_claude_error")
        return ResolveResult(found=False, notes=f"claude_error:{e!s}")

    raw = result.content.strip()
    if raw.startswith("```"):
        raw = raw.lstrip("`").lstrip("json").strip().rstrip("`").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("resolve_invalid_json", raw=raw[:200])
        return ResolveResult(found=False, notes="invalid_json", cost_inr_paise=result.cost_inr_paise)

    if not isinstance(data, dict) or not data.get("found"):
        return ResolveResult(
            found=False,
            confidence=float(data.get("confidence", 0.0)),
            notes=str(data.get("notes", "no_match")),
            cost_inr_paise=result.cost_inr_paise,
        )

    return ResolveResult(
        found=True,
        selector=dict(data.get("selector") or {}),
        action=str(data.get("action") or desired_action),
        params=dict(data.get("params") or {}),
        confidence=float(data.get("confidence", 0.0)),
        notes=str(data.get("notes", "")),
        cost_inr_paise=result.cost_inr_paise,
    )
