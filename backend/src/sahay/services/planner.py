"""Plan-first execution — Opus drafts a compact plan, Haiku executes.

Problem being solved
────────────────────
Pure step-by-step agents (Haiku alone) drift on 10+ step tasks: they
re-search already-found contacts, tap the same button twice, or lose
the thread of "why am I on this screen." The local-only view plus
short context window makes them myopic.

Solution
────────
On turn 0 of a conversation, run ONE Claude Opus 4.7 call with:
    • the user's goal
    • long-term memory about the user
    • the assistant's identity (name / personality)
    • a compact menu of what Sahay can do on a phone

Opus returns a typed plan: an ordered list of high-level steps with
an `expected_screen` and a `completion_hint` per step. The plan is
cached in Redis for the session (~1h TTL).

On every subsequent turn, Haiku gets the plan as additional context:
    "Your current plan:
       [✓] 1. Launch Netflix
       [✓] 2. Skip profile picker
       [→] 3. Tap search in bottom nav          ← current
       [ ] 4. Type 'Dune' in the search field
       [ ] 5. Tap first result
       [ ] 6. Tap Play"

Haiku no longer has to re-decide the whole task each step — it just
picks the concrete tap/type to accomplish the current plan item. A
re-plan is triggered only when the phone state diverges enough from
expected (tracked via a simple `failed_step_count` heuristic).

Cost
────
Opus plan: ~1000 tokens in, 600 out ≈ ₹8 per task.
Haiku execution: ~2000 in, 200 out per step ≈ ₹0.5.
Net: marginal cost bump, but tasks finish in fewer steps AND more
reliably. Break-even vs agent-loop-only around step 6-8.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sahay.config import get_settings
from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

settings = get_settings()
log = get_logger(__name__)


# ─── Plan data model ─────────────────────────────────────────────────


@dataclass(slots=True)
class PlanStep:
    index: int
    description: str                      # 1-line imperative ("Tap Search in bottom nav")
    expected_screen: str                  # what screen we expect to land on
    completion_hint: str                  # how to tell this step is done
    status: str = "pending"               # pending | in_progress | complete | failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "description": self.description,
            "expected_screen": self.expected_screen,
            "completion_hint": self.completion_hint,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanStep:
        return cls(
            index=int(d["index"]),
            description=str(d["description"])[:300],
            expected_screen=str(d.get("expected_screen", ""))[:200],
            completion_hint=str(d.get("completion_hint", ""))[:200],
            status=str(d.get("status", "pending")),
        )


@dataclass(slots=True)
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    model: str = ""
    cost_inr_paise: int = 0
    failed_step_count: int = 0            # consecutive turns of no progress

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "model": self.model,
            "cost_inr_paise": self.cost_inr_paise,
            "failed_step_count": self.failed_step_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        return cls(
            goal=str(d.get("goal", "")),
            steps=[PlanStep.from_dict(s) for s in d.get("steps", [])],
            model=str(d.get("model", "")),
            cost_inr_paise=int(d.get("cost_inr_paise", 0)),
            failed_step_count=int(d.get("failed_step_count", 0)),
        )

    # ─── Convenience ─────────────────────────────────────────────────

    def current_step(self) -> PlanStep | None:
        """First non-complete step."""
        for s in self.steps:
            if s.status != "complete":
                return s
        return None

    def all_complete(self) -> bool:
        return bool(self.steps) and all(s.status == "complete" for s in self.steps)


# ─── Planner prompt ──────────────────────────────────────────────────


_PLANNER_SYSTEM = """You are the planner for {assistant_name} — a phone-driving AI agent running on an Android device. A user has given a natural-language goal; your job is to break it into 3–8 concrete, ordered steps.

Each step must be:
  • a single observable action the agent will take
  • describable in ONE imperative sentence (<100 chars)
  • paired with what screen the agent should end up on
  • paired with a completion_hint — how to tell the step is done

Hard rules:
  1. PAYMENT / BANKING apps are off-limits (GPay, PhonePe, Paytm, bank apps, UPI). If the goal requires one, return a plan with a single `give_up` step explaining the block.
  2. 3–8 steps. Fewer is better. A well-designed plan has NO redundant "wait" or "confirm" steps — the executor handles waits implicitly.
  3. Reuse known user info from the LONG-TERM MEMORY block. If memory says "contact:amirtha → IT dept, SIM 1", the plan says "Tap Amirtha IT in the recents list" — not "search for Amirtha".
  4. No step should assume a fact that needs to be confirmed at runtime. If there could be multiple matches (two Amirthas), add a step that disambiguates (tap the first OR ask user).
  5. Output ONLY valid JSON matching this schema — no markdown, no prose, no code fences:

{{
  "goal": "<echo of the user's goal, cleaned up>",
  "steps": [
    {{
      "index": 1,
      "description": "Launch Netflix",
      "expected_screen": "Netflix home or profile picker",
      "completion_hint": "Netflix app is in foreground"
    }},
    ...
  ]
}}

Examples of GOOD steps:
  ✓ "Tap the Search icon in the bottom navigation"
  ✓ "Type 'Dune' in the search field"
  ✓ "Tap the first matching title tile"
  ✓ "Tap Play on the title details page"

Examples of BAD steps (don't do these):
  ✗ "Wait for 2 seconds"               (executor handles waits)
  ✗ "Open the app and navigate"         (too vague, collapse or split)
  ✗ "Maybe tap search if visible"       (no conditionals — the executor decides per screen)
"""


_PLANNER_USER_TEMPLATE = """Goal: {goal}

Long-term memory about this user:
{ltm_block}

Return the JSON plan now.
"""


# ─── Public API ──────────────────────────────────────────────────────


_PLAN_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def generate_plan(
    *,
    goal: str,
    assistant_name: str,
    ltm_block: str = "(no stored memories yet)",
) -> Plan:
    """Ask Opus 4.7 for a compact plan. Parses + validates before returning.

    On model / parse failure, returns a minimal one-step plan that falls
    back to pure step-by-step execution — caller keeps working.
    """
    system = _PLANNER_SYSTEM.format(assistant_name=assistant_name)
    user_msg = _PLANNER_USER_TEMPLATE.format(goal=goal.strip(), ltm_block=ltm_block)

    try:
        result = await complete(
            workload="ingestion",                 # Opus 4.7 — reserved for this + playbook ingest
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=1024,
            temperature=0.0,
        )
    except Exception as e:                        # noqa: BLE001
        log.exception("planner_failed")
        return _degenerate_plan(goal, reason=f"planner_exception:{e!s}")

    plan = _parse_plan(result.content, goal=goal)
    plan.model = result.model
    plan.cost_inr_paise = result.cost_inr_paise
    log.info(
        "plan_generated",
        goal_chars=len(goal),
        steps=len(plan.steps),
        model=result.model,
        cost_paise=result.cost_inr_paise,
    )
    return plan


def _parse_plan(raw: str, *, goal: str) -> Plan:
    """Tolerant JSON extractor — handles models that wrap in ```json fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    m = _PLAN_JSON_RE.search(text)
    if not m:
        return _degenerate_plan(goal, reason="planner_no_json_block")

    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return _degenerate_plan(goal, reason="planner_invalid_json")

    steps_raw = data.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        return _degenerate_plan(goal, reason="planner_no_steps")

    steps: list[PlanStep] = []
    for i, s in enumerate(steps_raw[:10], start=1):      # cap at 10 for safety
        if not isinstance(s, dict):
            continue
        steps.append(PlanStep(
            index=i,
            description=str(s.get("description", "")).strip()[:300],
            expected_screen=str(s.get("expected_screen", "")).strip()[:200],
            completion_hint=str(s.get("completion_hint", "")).strip()[:200],
            status="pending",
        ))

    if not steps:
        return _degenerate_plan(goal, reason="planner_empty_after_filter")

    return Plan(goal=str(data.get("goal", goal))[:1000], steps=steps)


def _degenerate_plan(goal: str, *, reason: str) -> Plan:
    """Minimal single-step plan — causes agent to fall back to pure step-by-step."""
    log.warning("degenerate_plan", reason=reason)
    return Plan(
        goal=goal,
        steps=[
            PlanStep(
                index=1,
                description="Accomplish the user's goal step by step",
                expected_screen="(varies)",
                completion_hint="goal achieved or user-visible success",
            )
        ],
        model="degenerate",
    )


# ─── Progress inference ──────────────────────────────────────────────


def mark_step_progress(plan: Plan, last_tool: str, last_outcome: str) -> None:
    """Mutate plan to reflect progress after a tool call.

    Heuristic:
      • If the last tool was `done` or `give_up` → all steps complete.
      • If the last tool succeeded (`dispatched` / `ok`) → current step
        advances to `complete` and the next becomes `in_progress`.
      • If failed repeatedly → bump failed_step_count; caller can use this
        to trigger a re-plan (see `should_replan`).
    """
    if not plan.steps:
        return

    if last_tool == "done":
        for s in plan.steps:
            s.status = "complete"
        return

    if last_tool == "give_up":
        for s in plan.steps:
            if s.status != "complete":
                s.status = "failed"
        return

    current = plan.current_step()
    if current is None:
        return

    if last_outcome in ("dispatched", "ok", "success"):
        current.status = "complete"
        plan.failed_step_count = 0
        nxt = plan.current_step()
        if nxt is not None:
            nxt.status = "in_progress"
    else:
        plan.failed_step_count += 1
        current.status = "in_progress"


def should_replan(plan: Plan, *, threshold: int = 4) -> bool:
    """True when the plan has stalled and we should ask Opus for a new one."""
    return plan.failed_step_count >= threshold


# ─── Prompt rendering ────────────────────────────────────────────────


def format_for_prompt(plan: Plan, max_chars: int = 1_200) -> str:
    """Render plan for injection into Haiku's system prompt."""
    if not plan.steps:
        return "(no plan — proceed step-by-step)"

    icon = {"pending": " ", "in_progress": "→", "complete": "✓", "failed": "✗"}
    lines = [f"GOAL: {plan.goal[:200]}", "STEPS:"]
    used = len(lines[0]) + len(lines[1])
    for s in plan.steps:
        line = f"  [{icon.get(s.status, ' ')}] {s.index}. {s.description}"
        if s.expected_screen:
            line += f"  (→ {s.expected_screen})"
        if used + len(line) + 1 > max_chars:
            lines.append("  …")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
