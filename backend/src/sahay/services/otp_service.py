"""Email OTP generation, verification, and rate-limit-aware issuance.

Codes are 6-digit numeric, bcrypt-hashed at rest, 5-minute expiry,
max 5 verification attempts per challenge.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

import bcrypt
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import OtpChallenge

OTP_TTL_SECONDS = 300       # 5 minutes
MAX_ATTEMPTS = 5
OTP_LENGTH = 6


def _generate_code() -> str:
    """Cryptographically random 6-digit code (zero-padded)."""
    n = secrets.randbelow(10 ** OTP_LENGTH)
    return str(n).zfill(OTP_LENGTH)


def _hash(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt(rounds=10)).decode()


def _verify(code: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode(), hashed.encode())
    except ValueError:
        return False


async def issue_otp(
    db: AsyncSession,
    email: str,
    purpose: Literal["signup", "login"],
    requested_ip: str | None = None,
) -> str:
    """Create a new OTP challenge and return the raw code (for sending).

    Caller is responsible for emailing the code via Resend.
    """
    code = _generate_code()
    challenge = OtpChallenge(
        email=email.lower(),
        code_hash=_hash(code),
        purpose=purpose,
        expires_at=datetime.now(UTC) + timedelta(seconds=OTP_TTL_SECONDS),
        requested_ip=requested_ip,
    )
    db.add(challenge)
    await db.flush()
    return code


async def verify_otp(
    db: AsyncSession,
    email: str,
    code: str,
    purpose: Literal["signup", "login"],
) -> bool:
    """Verify code against latest unconsumed challenge for (email, purpose).

    On success: marks consumed, returns True.
    On wrong code: increments attempts, returns False.
    On expired/exhausted/missing: returns False.
    """
    now = datetime.now(UTC)
    stmt = (
        select(OtpChallenge)
        .where(
            OtpChallenge.email == email.lower(),
            OtpChallenge.purpose == purpose,
            OtpChallenge.consumed_at.is_(None),
        )
        .order_by(desc(OtpChallenge.created_at))
        .limit(1)
    )
    challenge = (await db.execute(stmt)).scalar_one_or_none()

    if challenge is None:
        return False
    if challenge.expires_at < now:
        return False
    if challenge.attempts >= MAX_ATTEMPTS:
        return False

    if not _verify(code, challenge.code_hash):
        challenge.attempts += 1
        return False

    challenge.consumed_at = now
    return True


async def recent_request_count(
    db: AsyncSession,
    email: str,
    minutes: int = 10,
) -> int:
    """Count OTP requests for this email in the last N minutes.

    Used by the auth router for rate limiting (defense in depth alongside Redis).
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    stmt = (
        select(OtpChallenge.id)
        .where(
            OtpChallenge.email == email.lower(),
            OtpChallenge.created_at >= cutoff,
        )
    )
    rows = (await db.execute(stmt)).all()
    return len(rows)
