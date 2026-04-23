"""Resend client wrapper for sending OTP and other transactional emails.

Templates are inline (small set, multilingual). For bigger volumes
move to Resend's hosted React Email templates.
"""

from __future__ import annotations

from typing import Literal

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from sahay.config import get_settings
from sahay.util.logging import get_logger

settings = get_settings()
log = get_logger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


# ─── Templates (locale → subject + html + text) ──────────────────────


def _template_otp(code: str, purpose: str, locale: str) -> tuple[str, str, str]:
    """Returns (subject, html, plain_text)."""
    if locale.startswith("ta"):
        subject = f"Sahay – உங்கள் குறியீடு {code}"
        body_text = (
            f"வணக்கம்,\n\nஉங்கள் Sahay {purpose} குறியீடு: {code}\n"
            "இந்த குறியீடு 5 நிமிடங்களில் காலாவதியாகும்.\n\n— Sahay"
        )
    elif locale.startswith("hi"):
        subject = f"Sahay – आपका कोड {code}"
        body_text = (
            f"नमस्ते,\n\nआपका Sahay {purpose} कोड: {code}\n"
            "यह कोड 5 मिनट में समाप्त हो जाएगा।\n\n— Sahay"
        )
    else:
        subject = f"Sahay code: {code}"
        body_text = (
            f"Hi,\n\nYour Sahay {purpose} code is: {code}\n"
            "This code expires in 5 minutes.\n\n— Sahay"
        )
    body_html = f"""<!doctype html>
<html><body style="font-family:Inter,Arial,sans-serif;background:#FAF6E9;padding:32px;">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;">
    <h1 style="color:#3F3D9C;margin:0 0 16px;">Sahay</h1>
    <p style="font-size:16px;color:#222;">Your {purpose} code:</p>
    <p style="font-size:36px;font-weight:700;letter-spacing:6px;color:#3F3D9C;margin:16px 0;">{code}</p>
    <p style="font-size:14px;color:#666;">Expires in 5 minutes. If you didn't request this, ignore this email.</p>
  </div>
</body></html>"""
    return subject, body_html, body_text


# ─── Send ────────────────────────────────────────────────────────────


async def send_otp_email(
    *,
    to_email: str,
    code: str,
    purpose: Literal["signup", "login"],
    locale: str = "en",
) -> None:
    """Send an OTP email. Retries with exponential backoff on transient errors."""
    subject, html, text = _template_otp(code, purpose, locale)

    payload = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": subject,
        "html": html,
        "text": text,
        "reply_to": settings.resend_reply_to,
        "tags": [
            {"name": "category", "value": "otp"},
            {"name": "purpose", "value": purpose},
        ],
    }

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(_RESEND_URL, json=payload, headers=headers)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise httpx.HTTPStatusError(
                        "transient", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                log.info(
                    "otp_email_sent",
                    purpose=purpose,
                    locale=locale,
                    resend_id=resp.json().get("id"),
                )
