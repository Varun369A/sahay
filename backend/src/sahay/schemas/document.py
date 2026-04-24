"""Schemas for user-initiated / agent-initiated document creation."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DocKind = Literal["resume", "report", "presentation", "letter", "plain"]


class CreateDocumentRequest(BaseModel):
    kind: DocKind = Field(description="Document shape we'll render.")
    prompt: str = Field(
        min_length=4, max_length=4_000,
        description="Natural-language brief of what the doc should contain.",
    )
    title: str | None = Field(default=None, max_length=200)


class CreateDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    file_id: UUID
    name: str
    mime_type: str
    signed_url: str
    kind: str
    format: str
    tokens_used: int
    cost_inr_paise: int
