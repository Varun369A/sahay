"""User profile + app-grant schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Role values accepted by the API. Keep in sync with the Android role picker
# (io.sahay.ui.screens.settings.roles). "other" is the catch-all; null means
# the user hasn't picked yet.
ROLE_VALUES = {
    "student", "engineer", "founder", "doctor", "sales",
    "designer", "teacher", "writer", "lawyer", "manager",
    "researcher", "other",
}


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str | None
    locale: str
    tier: str
    role: str | None = None
    email_verified: bool
    created_at: datetime
    last_active_at: datetime | None


class UpdateMeRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    locale: str | None = Field(default=None, min_length=2, max_length=8)
    role: str | None = Field(default=None, max_length=32)


class GrantAppRequest(BaseModel):
    app_package: str = Field(min_length=3, max_length=255)


class AppGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    app_package: str
    granted_at: datetime
    revoked_at: datetime | None = None


class DeleteMyDataResponse(BaseModel):
    deleted: bool = True
    deleted_at: datetime
