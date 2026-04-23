"""Health + readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sahay import __version__
from sahay.db.session import get_db_session

router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness probe — does the process answer? No deps checked."""
    return {"status": "ok", "version": __version__}


@router.get("/ready", status_code=status.HTTP_200_OK)
async def ready(db: AsyncSession = Depends(get_db_session)) -> dict[str, str]:
    """Readiness probe — checks DB connectivity. Use for load balancer."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready"}
