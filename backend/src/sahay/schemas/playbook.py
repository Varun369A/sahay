"""Playbook API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlaybookLookupRequest(BaseModel):
    app_package: str = Field(min_length=3, max_length=255)
    intent: str = Field(min_length=2, max_length=64)
    app_version: str | None = Field(default=None, max_length=32)


class PlaybookContributeRequest(BaseModel):
    app_package: str
    app_name: str
    intent: str
    doc_url: str
    app_version: str | None = None


class PlaybookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    app_package: str
    app_name: str
    app_version: str | None
    intent: str
    steps_json: dict[str, Any]
    reliability_score: float
    popularity: int
    updated_at: datetime


class PlaybookSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    app_package: str
    app_name: str
    intent: str
    reliability_score: float
    popularity: int


class PlaybookOutcomeRequest(BaseModel):
    """Posted by the device at the end of every playbook execution.

    Drives `services.reliability_tracker.record_outcome` — Bayesian update
    on the parent playbook plus a row in `playbook_run_outcomes`.
    """
    playbook_id: UUID
    outcome: str = Field(min_length=1, max_length=32)
    failed_step_index: int | None = Field(default=None, ge=0)
    failed_reason: str | None = Field(default=None, max_length=200)
    duration_ms: int = Field(default=0, ge=0)
    # When set, this run was served from a canary revision; outcome
    # attribution credits THAT revision's counters instead of (or in
    # addition to) the parent's live counters.
    revision_id: UUID | None = None
    # Per-step record of which selector tier won the match. Keys are
    # step indexes as strings; values are tier names. Feeds the
    # selector_trainer aggregation that informs the Opus improver.
    selector_tiers: dict[str, str] | None = None


class PlaybookOutcomeResponse(BaseModel):
    """Echoed back so the client can show the user the new reliability."""
    counted: bool
    success_count: int
    fail_count: int
    expect_fail_count: int
    reliability_score: float
