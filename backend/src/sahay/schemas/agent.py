"""Agent-loop schemas.

The agent model:
  • Client sends current-screen observation (screenshot + tree) + goal + history
  • Claude replies with a single next tool call
  • Client executes that tool, loops back with new observation
  • Claude decides "done" when task complete, or "ask_user" when ambiguous

Tool calls are the only way the agent acts on the phone. This keeps
reasoning + action cleanly separated and auditable.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Observation from device ─────────────────────────────────────────


class AgentObservation(BaseModel):
    """Snapshot of what the phone looks like right now."""

    goal: str = Field(min_length=1, max_length=2000, description="User's overall goal")
    app_package: str | None = Field(default=None, max_length=255,
                                    description="Current foreground package")
    screen_tree_summary: str = Field(default="", max_length=25_000,
                                     description="Flattened accessibility tree JSON")
    screenshot_base64: str | None = Field(default=None,
                                          description="PNG screenshot base64 (optional; used by vision)")
    step_index: int = Field(default=0, ge=0, le=40)
    history: list[dict[str, Any]] = Field(default_factory=list,
                                          description="List of prior {tool, args, outcome}")
    screen_width: int | None = None
    screen_height: int | None = None


# ─── Tool calls Claude can issue ─────────────────────────────────────


class AgentTool(BaseModel):
    """A single tool call returned by the agent."""

    tool: Literal[
        "tap",              # tap at (x, y) OR on node matching selector
        "long_press",
        "type",             # type text into focused field OR selector
        "swipe",            # swipe up/down/left/right
        "wait",             # wait N ms for UI to settle
        "back",             # Android back
        "home",             # Android home
        "open_app",         # launch an app by package
        "speak",            # TTS speak text to user
        "ask_user",         # pause task, ask user a question
        "done",             # task complete
        "give_up",          # cannot continue
    ]
    # tap / long_press / type location
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    selector: dict[str, Any] = Field(default_factory=dict,
                                     description="Fallback selector (viewId/text/contentDescription)")
    # type
    text: str | None = Field(default=None, max_length=5000)
    # swipe
    direction: Literal["up", "down", "left", "right"] | None = None
    distance: int | None = Field(default=None, ge=10, le=3000)
    # wait
    duration_ms: int | None = Field(default=None, ge=50, le=10_000)
    # open_app
    app_package: str | None = Field(default=None, max_length=255)
    # speak / ask_user / done / give_up
    message: str | None = Field(default=None, max_length=2000)
    # Metadata
    reasoning: str | None = Field(default=None, max_length=500,
                                   description="Claude's 1-line rationale")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# ─── Request / response envelopes ────────────────────────────────────


class AgentStepRequest(BaseModel):
    observation: AgentObservation


class AgentStepResponse(BaseModel):
    action: AgentTool
    model: str
    tokens_used: int = 0
    cost_inr_paise: int = 0
