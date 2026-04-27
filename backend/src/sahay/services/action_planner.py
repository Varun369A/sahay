"""Action planner — given a current screen + intent, return next steps.

Two paths:
  1. KNOWN PLAYBOOK (preferred): use the cached steps_json directly, just
     resolve {{input}} placeholders against the user's params. Cheap.
  2. UNKNOWN PLAYBOOK or VISION FALLBACK: call Claude Sonnet with the
     current screen tree + intent and have it pick the next action.

Reliability score gates path 1 vs 2:
  • >= 0.9: trust the playbook fully
  • 0.7–0.9: playbook + per-step confirmation
  • < 0.7: Sonnet fallback
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook
from sahay.services.canary_router import pick_active_revision
from sahay.services.claude_client import complete
from sahay.services.playbook_ingestor import get_playbook
from sahay.util.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class PlanRequest:
    user_id: UUID
    app_package: str
    intent: str
    inputs: dict[str, Any]                       # e.g. {"recipient_name": "Amirtha", ...}
    screen_tree_summary: str | None = None       # text-flattened accessibility tree, optional
    app_version: str | None = None


@dataclass(slots=True)
class PlannedStep:
    step: int
    description: str
    target_screen: str
    action: str
    target_selector: dict[str, Any]
    params: dict[str, Any]
    confirm_required: bool
    timeout_ms: int
    on_failure: str | None = None


@dataclass(slots=True)
class PlanResult:
    steps: list[PlannedStep]
    source: str                                  # "playbook" | "vision"
    reliability_score: float
    model_used: str | None = None
    tokens_used: int = 0
    cost_inr_paise: int = 0
    notes: str | None = None
    needs_per_step_confirm: bool = False
    requires_inputs: list[str] = field(default_factory=list)
    # Carried back to the device so it knows which playbook to report
    # the run outcome against. None when source == "vision".
    playbook_id: str | None = None
    # Set when the served spec came from a canary revision rather than
    # the parent playbook's live spec. The device echoes this in the
    # outcome request so the canary's success/fail counters get the
    # right credit (and the parent doesn't).
    revision_id: str | None = None


# ─── Public API ──────────────────────────────────────────────────────


async def plan(db: AsyncSession, req: PlanRequest) -> PlanResult:
    """Decide the path and return a PlanResult."""
    pb = await get_playbook(db, req.app_package, req.intent, req.app_version)

    if pb is not None and float(pb.reliability_score) >= 0.7:
        # Canary check: if a recent canary revision exists AND this
        # user falls in the canary bucket, serve the canary spec
        # instead of the parent. Outcome attribution downstream credits
        # the canary's counters.
        decision = await pick_active_revision(
            db, playbook_id=pb.id, user_id=str(req.user_id),
        )
        if decision.use_canary and decision.spec_json is not None:
            log.info(
                "canary_served",
                playbook_id=str(pb.id),
                revision_id=str(decision.revision_id),
                bucket=decision.bucket,
            )
            return _plan_from_canary_spec(
                pb, decision.spec_json, decision.revision_id, req,
            )
        return _plan_from_playbook(pb, req)
    return await _plan_from_vision(pb, req)


def _plan_from_canary_spec(
    pb: Playbook,
    spec: dict[str, Any],
    revision_id: UUID | None,
    req: PlanRequest,
) -> PlanResult:
    """Same shape as `_plan_from_playbook` but the spec comes from a
    canary `PlaybookRevision`. Reliability score reported to the client
    is the parent's (canary doesn't yet have its own meaningful score)
    so behaviour like `needs_per_step_confirm` stays sane during the
    canary window."""
    declared_inputs: list[str] = spec.get("inputs", [])
    missing = [k for k in declared_inputs if k not in req.inputs]
    if missing:
        return PlanResult(
            steps=[],
            source="playbook",
            reliability_score=float(pb.reliability_score),
            playbook_id=str(pb.id),
            revision_id=str(revision_id) if revision_id else None,
            requires_inputs=missing,
        )

    raw_steps: list[dict[str, Any]] = spec.get("steps", [])
    resolved = [_resolve_step(s, req.inputs) for s in raw_steps]
    return PlanResult(
        steps=resolved,
        source="playbook",
        reliability_score=float(pb.reliability_score),
        playbook_id=str(pb.id),
        revision_id=str(revision_id) if revision_id else None,
        notes=spec.get("notes"),
        needs_per_step_confirm=float(pb.reliability_score) < 0.9,
    )


# ─── Playbook path ───────────────────────────────────────────────────


def _plan_from_playbook(pb: Playbook, req: PlanRequest) -> PlanResult:
    spec: dict[str, Any] = pb.steps_json
    declared_inputs: list[str] = spec.get("inputs", [])

    missing = [k for k in declared_inputs if k not in req.inputs]
    if missing:
        return PlanResult(
            steps=[],
            source="playbook",
            reliability_score=float(pb.reliability_score),
        playbook_id=str(pb.id),
            requires_inputs=missing,
        )

    raw_steps: list[dict[str, Any]] = spec.get("steps", [])
    resolved = [_resolve_step(s, req.inputs) for s in raw_steps]
    return PlanResult(
        steps=resolved,
        source="playbook",
        reliability_score=float(pb.reliability_score),
        playbook_id=str(pb.id),
        notes=spec.get("notes"),
        needs_per_step_confirm=float(pb.reliability_score) < 0.9,
    )


def _resolve_step(raw: dict[str, Any], inputs: dict[str, Any]) -> PlannedStep:
    """Substitute {{input}} placeholders and coerce to PlannedStep."""

    def sub(value: Any) -> Any:
        if isinstance(value, str):
            for k, v in inputs.items():
                value = value.replace(f"{{{{{k}}}}}", str(v))
        elif isinstance(value, dict):
            return {k: sub(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [sub(x) for x in value]
        return value

    return PlannedStep(
        step=int(raw["step"]),
        description=str(raw["description"]),
        target_screen=str(raw["target_screen"]),
        action=str(raw["action"]),
        target_selector=sub(raw.get("target_selector", {})) or {},
        params=sub(raw.get("params", {})) or {},
        confirm_required=bool(raw.get("confirm_required", False)),
        timeout_ms=int(raw.get("timeout_ms", 5000)),
        on_failure=raw.get("on_failure"),
    )


# ─── Vision / Sonnet fallback ────────────────────────────────────────


_VISION_SYSTEM = """You are Sahay's runtime planner. You receive:
  • An app package + an intent
  • Optionally a partial playbook (low reliability) and the current screen tree
  • The user's inputs

Output JSON with the next 1-3 steps to take. Each step uses accessibility-tree selectors.
Schema (one step):
  {"step": 1, "description": "...", "target_screen": "...",
   "action": "launch|tap|long_press|type|swipe|wait|read|verify|back|home",
   "target_selector": {...}, "params": {...},
   "confirm_required": false, "timeout_ms": 5000, "on_failure": null}

Hard rules:
  • NEVER plan steps in payment/banking apps. Return [] if asked.
  • Mark confirm_required=true for sends/deletes/posts.
  • Output ONLY JSON: {"steps": [...]}
"""


async def _plan_from_vision(pb: Playbook | None, req: PlanRequest) -> PlanResult:
    user_payload = {
        "app_package": req.app_package,
        "intent": req.intent,
        "inputs": req.inputs,
        "screen_tree": (req.screen_tree_summary or "")[:8000],
        "playbook_hint": pb.steps_json if pb else None,
    }
    res = await complete(
        workload="quality",
        system=_VISION_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(user_payload)}],
        max_tokens=1024,
        temperature=0.0,
    )

    try:
        data = json.loads(res.content.strip().lstrip("`").lstrip("json").strip("`").strip())
    except json.JSONDecodeError:
        log.warning("vision_planner_invalid_json")
        return PlanResult(
            steps=[],
            source="vision",
            reliability_score=float(pb.reliability_score) if pb else 0.0,
            model_used=res.model,
            tokens_used=res.input_tokens + res.output_tokens,
            cost_inr_paise=res.cost_inr_paise,
            notes="planner_returned_invalid_json",
        )

    raw_steps = data.get("steps", []) if isinstance(data, dict) else []
    steps = [_resolve_step(s, req.inputs) for s in raw_steps]

    return PlanResult(
        steps=steps,
        source="vision",
        reliability_score=float(pb.reliability_score) if pb else 0.0,
        model_used=res.model,
        tokens_used=res.input_tokens + res.output_tokens,
        cost_inr_paise=res.cost_inr_paise,
        needs_per_step_confirm=True,    # always confirm on vision fallback
    )
