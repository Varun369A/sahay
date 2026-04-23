"""JWT issuance + verification + refresh token management.

Access tokens: short-lived (15 min), HS256, contains user_id + tier.
Refresh tokens: random 32-byte secret (NOT a JWT), bcrypt-hashed in DB,
rotated on every refresh (one-time use).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.config import get_settings
from sahay.db.models import RefreshToken, User

settings = get_settings()


# ─── Access tokens (JWT) ─────────────────────────────────────────────


def create_access_token(user: User) -> tuple[str, int]:
    """Returns (jwt, expires_in_seconds)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "tier": user.tier,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": "sahay",
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, settings.access_token_ttl_minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    """Returns the JWT payload. Raises JWTError on invalid/expired."""
    return jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        issuer="sahay",
    )


# ─── Refresh tokens (opaque random) ──────────────────────────────────


def _hash_refresh(token: str) -> str:
    """SHA-256 of refresh token. Fast equality check (vs bcrypt for OTP)."""
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(
    db: AsyncSession,
    user: User,
    device_id: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Generate a new refresh token, persist hash, return raw token."""
    raw = secrets.token_urlsafe(32)  # 256 bits of entropy
    rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh(raw),
        device_id=device_id,
        user_agent=user_agent[:255] if user_agent else None,
        expires_at=datetime.now(timezone.utc)
                   + timedelta(days=settings.refresh_token_ttl_days),
    )
    db.add(rt)
    await db.flush()
    return raw


async def rotate_refresh_token(
    db: AsyncSession,
    raw_token: str,
    device_id: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str]:
    """One-time-use rotation. Validates → revokes old → issues new.

    Raises InvalidRefreshToken on any failure.
    """
    digest = _hash_refresh(raw_token)
    now = datetime.now(timezone.utc)

    stmt = select(RefreshToken).where(RefreshToken.token_hash == digest)
    rt = (await db.execute(stmt)).scalar_one_or_none()

    if rt is None:
        raise InvalidRefreshToken("not_found")
    if rt.revoked_at is not None:
        # Reuse attempt → security incident. Revoke ALL tokens for this user.
        await _revoke_all_for_user(db, rt.user_id)
        raise InvalidRefreshToken("reused")
    if rt.expires_at < now:
        raise InvalidRefreshToken("expired")

    rt.revoked_at = now
    user = (await db.execute(select(User).where(User.id == rt.user_id))).scalar_one()

    new_raw = await issue_refresh_token(db, user, device_id, user_agent)
    return user, new_raw


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    """Logout — revoke a single token. No-op if not found."""
    digest = _hash_refresh(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == digest)
    rt = (await db.execute(stmt)).scalar_one_or_none()
    if rt and rt.revoked_at is None:
        rt.revoked_at = datetime.now(timezone.utc)


async def _revoke_all_for_user(db: AsyncSession, user_id: UUID) -> None:
    stmt = select(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    rows = (await db.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)
    for rt in rows:
        rt.revoked_at = now


# ─── Errors ──────────────────────────────────────────────────────────


class InvalidRefreshToken(Exception):
    """Refresh token is invalid, expired, or has been reused."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InvalidAccessToken(Exception):
    """Access token failed verification."""


def safe_decode_access_token(token: str) -> dict[str, Any]:
    """Wraps decode with our own exception so callers don't import jose."""
    try:
        return decode_access_token(token)
    except JWTError as e:
        raise InvalidAccessToken(str(e)) from e
