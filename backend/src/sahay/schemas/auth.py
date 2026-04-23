"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    locale: str = Field(default="en", min_length=2, max_length=8)


class LoginRequest(BaseModel):
    email: EmailStr


class OtpVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    purpose: Literal["signup", "login"]
    device_id: str | None = Field(default=None, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until access_token expires


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    display_name: str | None
    locale: str
    tier: str
    email_verified: bool
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserPublic
    tokens: TokenPair


class OtpSentResponse(BaseModel):
    message: str = "If the email is valid, a code has been sent."
    expires_in_seconds: int = 300
