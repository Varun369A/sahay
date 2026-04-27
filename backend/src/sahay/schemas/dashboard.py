"""Wire-format for the admin dashboard endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HeadlineOut(BaseModel):
    window_days: int
    total_runs: int
    successes: int
    failures: int
    aborted: int
    success_rate: float
    mean_success_duration_ms: int
    distinct_users: int
    distinct_playbooks: int


class IntentRowOut(BaseModel):
    app_package: str
    app_name: str
    intent: str
    reliability_score: float
    success_count_lifetime: int
    fail_count_lifetime: int
    runs_in_window: int
    successes_in_window: int
    success_rate_in_window: float


class FailureClassOut(BaseModel):
    failure_class: str
    count: int
    pct_of_failures: float


class CanaryOut(BaseModel):
    revision_id: str
    playbook_id: str
    app_package: str
    intent: str
    version: int
    canary_score: float
    parent_baseline: float
    canary_runs: int
    age_hours: float
    live_decision: str


class RecentPromotionOut(BaseModel):
    revision_id: str
    playbook_id: str
    app_package: str
    intent: str
    promoted_at: datetime
    notes: str | None = None


class DashboardOut(BaseModel):
    generated_at: datetime
    headline: HeadlineOut
    intents: list[IntentRowOut] = Field(default_factory=list)
    failure_classes: list[FailureClassOut] = Field(default_factory=list)
    canaries: list[CanaryOut] = Field(default_factory=list)
    recent_promotions: list[RecentPromotionOut] = Field(default_factory=list)
