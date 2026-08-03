"""Reusable FastAPI dependencies — auth, RLS context, rate limit.

Every authenticated route should depend on `current_user`. This:
  1. Validates the access token
  2. Sets `app.user_id` on the DB session (activates RLS)
  3. Returns the loaded User row
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User
from sahay.db.session import SessionLocal
from sahay.services.jwt_service import (
    InvalidAccessToken,
    safe_decode_access_token,
)

_BEARER = "Bearer "


async def get_db_session_with_rls(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """DB session with RLS context set from request state.

    Used by `current_user` after auth — the user_id is set BEFORE any
    user-table query runs so RLS policies fire correctly.
    """
    user_id: UUID | None = getattr(request.state, "user_id", None)
    async with SessionLocal() as session:
        try:
            if user_id is not None:
                # SET LOCAL is per-transaction. asyncpg uses prepared statements
                # so we use parameterised text() with bind for safety.
                await session.execute(
                    text("SELECT set_config('app.user_id', :uid, true)").bindparams(
                        uid=str(user_id)
                    )
                )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> User:
    """Resolve the bearer token → User. Sets request.state.user_id for RLS."""
    if not authorization or not authorization.startswith(_BEARER):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_bearer_token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[len(_BEARER):].strip()
    try:
        payload = safe_decode_access_token(token)
    except InvalidAccessToken:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed_token",
        ) from None

    request.state.user_id = user_id

    # Use a one-off session here to load the user (RLS not yet needed for users.id lookup).
    async with SessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)").bindparams(
                uid=str(user_id)
            )
        )
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_not_found",
        )
    return user


def require_tier(*allowed: str):
    """Factory: dependency that enforces tier. Use as Depends(require_tier('pro'))."""

    async def _check(user: User = Depends(current_user)) -> User:
        if user.tier not in allowed:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"tier_required:{','.join(allowed)}",
            )
        return user

    return _check


def client_ip(request: Request) -> str | None:
    """Resolve client IP from the real connection, ignoring X-Forwarded-For.

    X-Forwarded-For is user-controlled and must not be trusted for
    rate-limit keying unless the app sits behind a known trusted proxy
    that overwrites the header. Using the raw transport address prevents
    header-forging to bypass per-IP rate limits.
    """
    return request.client.host if request.client else None
