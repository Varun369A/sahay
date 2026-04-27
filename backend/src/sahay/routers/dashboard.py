"""Admin dashboard endpoint — single read, full ops + investor view.

Gated to enterprise tier (the project's admin tier). One GET that returns
everything the admin UI needs to render the entire ops view, refreshed
~once/minute.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User
from sahay.db.session import get_db_session
from sahay.deps import require_tier
from sahay.schemas.dashboard import DashboardOut
from sahay.services.dashboard import DEFAULT_WINDOW_DAYS, snapshot

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    window_days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=90),
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(require_tier("enterprise")),
) -> DashboardOut:
    snap = await snapshot(db, window_days=window_days)
    # Convert dataclasses to dicts so Pydantic can validate; field names
    # on the dataclass and Pydantic schema are aligned by design.
    return DashboardOut.model_validate(asdict(snap))
