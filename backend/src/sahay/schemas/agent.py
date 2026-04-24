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
    conversation_id: str = Field(min_length=1, max_length=64,
                                 description="Stable id scoping turn history + PII map")
    app_package: str | None = Field(default=None, max_length=255,
                                    description="Current foreground package")
    screen_tree_summary: str = Field(default="", max_length=25_000,
                                     description="Flattened accessibility tree JSON")
    screenshot_base64: str | None = Field(default=None,
                                          description="PNG screenshot base64 (optional; used by vision)")
    step_index: int = Field(default=0, ge=0, le=40)
    history: list[dict[str, Any]] = Field(default_factory=list,
                                          description="Prior turns — server augments with Redis-held memory")
    screen_width: int | None = None
    screen_height: int | None = None
    assistant_name: str = Field(default="Sahay", max_length=40,
                                description="User's chosen assistant name — used for personality")


# ─── Tool calls Claude can issue ─────────────────────────────────────


class AgentTool(BaseModel):
    """A single tool call returned by the agent."""

    tool: Literal[
        "tap_node",         # ★ preferred — tap indexed node from current tree
        "type_in_node",     # ★ preferred — type into indexed editable node
        "long_press_node",  # long-press indexed node (voice msgs, context menus)
        "tap",              # tap at (x, y) coords — use when target only visible in screenshot
        "long_press",
        "type",             # type text into currently focused field
        "swipe",            # swipe up/down/left/right
        "wait",             # wait N ms for UI to settle
        "back",             # Android back
        "home",             # Android home
        "open_app",         # launch an app by package
        "speak",            # TTS speak text to user
        "ask_user",         # pause task, ask user a question
        "done",             # task complete
        "give_up",          # cannot continue
        "remember",         # persist a fact about the user (long-term memory)
        "forget",           # delete a fact from long-term memory
        "create_document",  # create resume/report/presentation/letter (server-side)
        "stage_reply",         # stage a notification reply + ask for confirmation (server-side)
        "reply_to_notification",  # fire RemoteInput reply to a prior notification (client-side)
        "note_followup",    # persist an open-loop commitment (server-side)
    ]
    # tap_node / type_in_node — the `i` from the tree nodes list
    node_index: int | None = Field(default=None, ge=0, le=500)
    # tap / long_press coordinates
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
    # remember / forget
    key: str | None = Field(default=None, max_length=128)
    value: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=32)
    importance: int | None = Field(default=None, ge=1, le=10)
    # create_document
    kind: str | None = Field(default=None, max_length=32,
                             description="Document kind: resume|report|presentation|letter|plain")
    prompt: str | None = Field(default=None, max_length=4000,
                               description="Doc brief — what the content should be.")
    # stage_reply / reply_to_notification
    notification_key: str | None = Field(default=None, max_length=200,
                                         description="Android SBN key prefix of the "
                                                     "notification to reply to")
    contact: str | None = Field(default=None, max_length=120,
                                description="Human-readable name of the reply target")
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
