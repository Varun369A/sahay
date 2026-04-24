"""Pydantic schemas for the notification-listener feature.

The Android NotificationListenerService forwards new notifications to
`POST /v1/notifications/ingest` in small batches (typically 1-5 at a time,
with client-side debounce). The agent queries `GET /v1/notifications/recent`
internally when building its prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NotificationIn(BaseModel):
    """One notification forwarded from the device."""

    model_config = ConfigDict(str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=200,
                     description="Android StatusBarNotification.getKey() — used for dedup")
    package: str = Field(min_length=1, max_length=200,
                         description="e.g. com.whatsapp")
    app_name: str | None = Field(default=None, max_length=80,
                                 description="Human-readable app label")
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=1000,
                             description="Body text — truncated by Android API anyway")
    category: str | None = Field(default=None, max_length=40,
                                 description="Android Notification.CATEGORY_* (msg, email, …)")
    posted_at: float = Field(
        description="Unix seconds. Android System.currentTimeMillis()/1000.",
    )
    is_ongoing: bool = Field(default=False,
                             description="Persistent notifications (music, nav) — deprioritised")


class NotificationIngestRequest(BaseModel):
    notifications: list[NotificationIn] = Field(min_length=1, max_length=50)


class NotificationIngestResponse(BaseModel):
    stored: int = Field(description="How many were kept (after dedup + payment-app filter)")
    dropped: int = Field(description="How many were filtered out")


class NotificationOut(BaseModel):
    """Shape returned to callers of /recent."""

    model_config = ConfigDict(str_strip_whitespace=True)

    key: str
    package: str
    app_name: str
    title: str
    text: str
    category: str
    posted_at: float
    is_ongoing: bool
