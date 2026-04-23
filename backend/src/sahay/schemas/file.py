"""File API schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    folder: str
    name: str
    mime_type: str
    size_bytes: int | None
    parent_id: UUID | None
    source: str
    created_at: datetime
    download_url: str | None = None


class GenerateImageRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    folder: str = Field(default="/", max_length=255)
    name: str | None = Field(default=None, max_length=255)
    aspect_ratio: str = Field(default="1:1", pattern=r"^(1:1|3:2|2:3|16:9|9:16|4:3|3:4)$")
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class EditImageRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)


class ListFilesQuery(BaseModel):
    folder: str = "/"
    limit: int = Field(default=50, ge=1, le=200)
