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
