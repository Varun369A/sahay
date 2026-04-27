"""Action planner / log API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanRequest(BaseModel):
    app_package: str = Field(min_length=3, max_length=255)
    intent: str = Field(min_length=2, max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict)
    screen_tree_summary: str | None = Field(default=None, max_length=10_000)
    app_version: str | None = Field(default=None, max_length=32)


class PlannedStepOut(BaseModel):
    step: int
    description: str
    target_screen: str
    action: str
    target_selector: dict[str, Any]
    params: dict[str, Any]
    confirm_required: bool
    timeout_ms: int
    on_failure: str | None = None


class PlanResponse(BaseModel):
    steps: list[PlannedStepOut]
    source: Literal["playbook", "vision"]
    reliability_score: float
    needs_per_step_confirm: bool
    requires_inputs: list[str] = Field(default_factory=list)
    notes: str | None = None
    # Set when the plan was sourced from a stored playbook. The device
    # uses this to report the run outcome via /v1/playbooks/outcome.
    playbook_id: str | None = None
    # Set when the served spec came from a canary revision instead of
    # the parent playbook's live spec. The device echoes this back on
    # outcome so canary counters get the right credit.
    revision_id: str | None = None


class ActionLogRequest(BaseModel):
    app_package: str
    intent: str
    status: Literal["success", "failed", "aborted", "needs_confirm"]
    duration_ms: int | None = None
    failure_reason: str | None = Field(default=None, max_length=255)
    user_aborted: bool = False


class DispatchRequest(BaseModel):
    utterance: str = Field(min_length=1, max_length=2000)
    locale: str = Field(default="en", min_length=2, max_length=8)


class DispatchResponse(BaseModel):
    matched: bool
    app_package: str | None = None
    intent: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    notes: str | None = None


class ResolveRequest(BaseModel):
    """When a fixed selector misses on-device, ask Claude to pick the right
    node from the live accessibility tree."""
    app_package: str = Field(min_length=3, max_length=255)
    step_description: str = Field(max_length=300)
    desired_action: str = Field(pattern=r"^(tap|long_press|type|swipe|wait|read|verify|back|home)$")
    screen_tree_summary: str = Field(max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ResolveResponse(BaseModel):
    found: bool
    selector: dict[str, Any] = Field(default_factory=dict)
    action: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    notes: str | None = None


class UsageOut(BaseModel):
    daily_count: int
    daily_limit: int
    weekly_count: int
    weekly_limit: int
    monthly_image_gens_used: int
    monthly_image_gens_limit: int
    tier: str
