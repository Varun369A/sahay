"""Playbook lookup + contribute endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import Playbook, User
from sahay.db.session import get_db_session
from sahay.deps import current_user
from sahay.domain.payment_blocklist import is_payment_app
from sahay.schemas.playbook import (
    PlaybookContributeRequest,
    PlaybookLookupRequest,
    PlaybookOut,
    PlaybookSummary,
)
from sahay.services.playbook_ingestor import get_playbook, ingest_playbook
from sahay.services.rate_limiter import rate_limit
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1/playbooks", tags=["playbooks"])
log = get_logger(__name__)


@router.post(
    "/lookup",
    response_model=PlaybookOut,
    dependencies=[Depends(rate_limit("playbooks", per_minute=120))],
)
async def lookup(
    body: PlaybookLookupRequest,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(current_user),
) -> PlaybookOut:
    if is_payment_app(body.app_package):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="payment_app_blocked",
        )
    pb = await get_playbook(db, body.app_package, body.intent, body.app_version)
    if pb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="playbook_not_found",
        )
    pb.popularity += 1
    return PlaybookOut.model_validate(pb)


@router.post(
    "/contribute",
    response_model=PlaybookOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("playbooks_write", per_minute=10))],
)
async def contribute(
    body: PlaybookContributeRequest,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(current_user),
) -> PlaybookOut:
    """Trigger ingestion of a new playbook from an official doc URL.

    Currently open to all authenticated users; in production this should be
    gated to admins or a community-review queue (see open questions in spec).
    """
    if is_payment_app(body.app_package):
        raise HTTPException(403, "payment_app_blocked")
    try:
        pb = await ingest_playbook(
            db,
            app_package=body.app_package,
            app_name=body.app_name,
            intent=body.intent,
            doc_url=body.doc_url,
            app_version=body.app_version,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from None
    return PlaybookOut.model_validate(pb)


@router.get(
    "/popular",
    response_model=list[PlaybookSummary],
    dependencies=[Depends(rate_limit("playbooks", per_minute=60))],
)
async def popular(
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(current_user),
) -> list[PlaybookSummary]:
    limit = max(1, min(limit, 200))
    stmt = (
        select(Playbook)
        .where(Playbook.is_payment_app.is_(False))
        .order_by(desc(Playbook.popularity))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [PlaybookSummary.model_validate(r) for r in rows]
