"""User profile + app allowlist + privacy (delete-my-data) endpoints.

DPDP / GDPR right-to-erasure is in this router. Cascade delete from `users`
takes care of: refresh_tokens, app_grants, action_logs, files, subscription,
usage_periods. We additionally purge R2 prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete as sqldelete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User, UserAppGrant
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.domain.payment_blocklist import is_payment_app
from sahay.schemas.user import (
    ROLE_VALUES,
    AppGrantOut,
    DeleteMyDataResponse,
    GrantAppRequest,
    MeResponse,
    UpdateMeRequest,
)
from sahay.services import r2_client
from sahay.services.rate_limiter import rate_limit
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1", tags=["users"])
log = get_logger(__name__)


# ─── /me ────────────────────────────────────────────────────────────


@router.get("/me", response_model=MeResponse)
async def me(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> MeResponse:
    user.last_active_at = datetime.now(UTC)
    return MeResponse.model_validate(user)


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateMeRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> MeResponse:
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.locale is not None:
        user.locale = body.locale
    if body.role is not None:
        normalized = body.role.strip().lower()
        if normalized not in ROLE_VALUES:
            raise HTTPException(422, "invalid_role")
        user.role = normalized
    await db.flush()
    return MeResponse.model_validate(user)


# ─── App grants (allowlist) ─────────────────────────────────────────


@router.get("/me/app-grants", response_model=list[AppGrantOut])
async def list_grants(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> list[AppGrantOut]:
    stmt = select(UserAppGrant).where(
        UserAppGrant.user_id == user.id,
        UserAppGrant.revoked_at.is_(None),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [AppGrantOut.model_validate(r) for r in rows]


@router.post(
    "/me/app-grants",
    response_model=AppGrantOut,
    status_code=status.HTTP_201_CREATED,
)
async def grant_app(
    body: GrantAppRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> AppGrantOut:
    if is_payment_app(body.app_package):
        raise HTTPException(403, "payment_app_blocked")

    existing = await db.get(UserAppGrant, (user.id, body.app_package))
    if existing is not None:
        existing.revoked_at = None
        await db.flush()
        return AppGrantOut.model_validate(existing)

    grant = UserAppGrant(user_id=user.id, app_package=body.app_package)
    db.add(grant)
    await db.flush()
    return AppGrantOut.model_validate(grant)


@router.delete("/me/app-grants/{app_package}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_app(
    app_package: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> None:
    grant = await db.get(UserAppGrant, (user.id, app_package))
    if grant is None or grant.revoked_at is not None:
        raise HTTPException(404, "grant_not_found")
    grant.revoked_at = datetime.now(UTC)


# ─── Privacy: delete my data ────────────────────────────────────────


@router.delete(
    "/me",
    response_model=DeleteMyDataResponse,
    dependencies=[Depends(rate_limit("delete_account", per_minute=2))],
)
async def delete_my_data(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(current_user),
) -> DeleteMyDataResponse:
    """DPDP / GDPR right to erasure.

    Steps:
      1. Wipe all R2 objects under users/{user_id}/
      2. Delete user row → cascades to all user-scoped tables
    """
    user_id = user.id
    log.info("delete_my_data_start", user_id=str(user_id))

    await r2_client.delete_user_prefix(user_id)
    await db.execute(sqldelete(User).where(User.id == user_id))

    log.info("delete_my_data_complete", user_id=str(user_id))
    return DeleteMyDataResponse(deleted_at=datetime.now(UTC))
