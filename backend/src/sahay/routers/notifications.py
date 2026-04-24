"""Notification listener endpoints.

Paths
─────
POST   /v1/notifications/ingest   — Android listener POSTs batches here
GET    /v1/notifications/recent   — debug / agent helper: last N notifications
DELETE /v1/notifications          — user-initiated wipe (privacy)

Security
────────
• `current_user` dep authenticates every call — never anonymous.
• Payment / banking / UPI / wallet packages are dropped at the store layer
  regardless of what the client sends (defense in depth).
• Per-user keyed storage; no tenant leaks possible.

Rate limits
───────────
Ingest is generous (120/min) because phones burst on screen-unlock.
Reads and wipes are tighter.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from sahay.db.models import User
from sahay.deps import current_user
from sahay.schemas.notifications import (
    NotificationIn, NotificationIngestRequest, NotificationIngestResponse,
    NotificationOut,
)
from sahay.services.notification_store import (
    Notification, get_notification_store,
)
from sahay.services.rate_limiter import rate_limit

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


class WipeResponse(BaseModel):
    deleted: int


def _to_domain(n: NotificationIn) -> Notification:
    return Notification(
        key=n.key,
        package=n.package,
        app_name=n.app_name or "",
        title=n.title or "",
        text=n.text or "",
        category=n.category or "",
        posted_at=float(n.posted_at),
        is_ongoing=bool(n.is_ongoing),
    )


def _to_out(n: Notification) -> NotificationOut:
    return NotificationOut(
        key=n.key,
        package=n.package,
        app_name=n.app_name,
        title=n.title,
        text=n.text,
        category=n.category,
        posted_at=n.posted_at,
        is_ongoing=n.is_ongoing,
    )


@router.post(
    "/ingest",
    response_model=NotificationIngestResponse,
    dependencies=[Depends(rate_limit("notif_ingest", per_minute=120))],
)
async def ingest_notifications(
    body: NotificationIngestRequest,
    user: User = Depends(current_user),
) -> NotificationIngestResponse:
    store = get_notification_store()
    domain = [_to_domain(n) for n in body.notifications]
    stored = await store.ingest(str(user.id), domain)
    dropped = len(domain) - stored
    return NotificationIngestResponse(stored=stored, dropped=dropped)


@router.get(
    "/recent",
    response_model=list[NotificationOut],
    dependencies=[Depends(rate_limit("notif_read", per_minute=60))],
)
async def recent_notifications(
    limit: int = Query(default=10, ge=1, le=50),
    since_seconds: int | None = Query(default=None, ge=0, le=30 * 60),
    package: str | None = Query(default=None, max_length=200),
    user: User = Depends(current_user),
) -> list[NotificationOut]:
    store = get_notification_store()
    items = await store.recent(
        str(user.id),
        limit=limit,
        since_seconds=since_seconds,
        package=package,
    )
    return [_to_out(n) for n in items]


@router.delete(
    "",
    response_model=WipeResponse,
    dependencies=[Depends(rate_limit("notif_wipe", per_minute=6))],
)
async def wipe_notifications(
    user: User = Depends(current_user),
) -> WipeResponse:
    store = get_notification_store()
    deleted = await store.clear(str(user.id))
    return WipeResponse(deleted=deleted)
