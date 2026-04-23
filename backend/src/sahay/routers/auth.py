"""Authentication endpoints — email-OTP signup/login + JWT.

Notes:
  • Signup and login both issue an OTP. Verify endpoint distinguishes by `purpose`.
  • Response is identical whether email exists or not (no enumeration).
  • Refresh tokens rotate on every use; reuse triggers full revocation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import User
from sahay.db.session import get_db_session
from sahay.deps import client_ip, current_user
from sahay.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    OtpSentResponse,
    OtpVerifyRequest,
    RefreshRequest,
    SignupRequest,
    TokenPair,
    UserPublic,
)
from sahay.services import jwt_service, otp_service
from sahay.services.rate_limiter import rate_limit
from sahay.services.resend_client import send_otp_email
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1/auth", tags=["auth"])
log = get_logger(__name__)


# ─── Signup ──────────────────────────────────────────────────────────


@router.post(
    "/signup",
    response_model=OtpSentResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("auth", per_minute=5))],
)
async def signup(
    body: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> OtpSentResponse:
    """Send OTP for new account. Idempotent — same response if user exists."""
    # Soft burst limiter on top of Redis rate-limit.
    if await otp_service.recent_request_count(db, body.email, minutes=10) >= 3:
        raise HTTPException(status_code=429, detail="too_many_recent_requests")

    code = await otp_service.issue_otp(
        db, body.email, "signup", requested_ip=client_ip(request)
    )
    await send_otp_email(
        to_email=body.email,
        code=code,
        purpose="signup",
        locale=body.locale,
    )
    log.info("signup_otp_sent", email_domain=body.email.split("@")[-1])
    return OtpSentResponse()


# ─── Login ───────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=OtpSentResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("auth", per_minute=5))],
)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> OtpSentResponse:
    """Send OTP for existing account. Same response shape as signup."""
    if await otp_service.recent_request_count(db, body.email, minutes=10) >= 3:
        raise HTTPException(status_code=429, detail="too_many_recent_requests")

    user = await _user_by_email(db, body.email)
    locale = user.locale if user else "en"

    code = await otp_service.issue_otp(
        db, body.email, "login", requested_ip=client_ip(request)
    )
    await send_otp_email(
        to_email=body.email,
        code=code,
        purpose="login",
        locale=locale,
    )
    log.info("login_otp_sent", email_domain=body.email.split("@")[-1])
    return OtpSentResponse()


# ─── Verify OTP → tokens ─────────────────────────────────────────────


@router.post(
    "/verify-otp",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("auth", per_minute=10))],
)
async def verify_otp(
    body: OtpVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    ok = await otp_service.verify_otp(db, body.email, body.code, body.purpose)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid_or_expired_code")

    user = await _user_by_email(db, body.email)
    if body.purpose == "signup":
        if user is not None:
            # Treat as login — user already exists, OTP was valid.
            pass
        else:
            user = User(email=body.email.lower(), email_verified=True, locale="en")
            db.add(user)
            await db.flush()

    if user is None:
        # Login on non-existent account — should never happen with correct flow.
        raise HTTPException(status_code=404, detail="user_not_found")

    user.email_verified = True

    access, expires_in = jwt_service.create_access_token(user)
    refresh = await jwt_service.issue_refresh_token(
        db, user,
        device_id=body.device_id,
        user_agent=request.headers.get("user-agent"),
    )

    log.info("auth_success", user_id=str(user.id), purpose=body.purpose)

    return AuthResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=expires_in,
        ),
    )


# ─── Refresh ─────────────────────────────────────────────────────────


@router.post(
    "/refresh",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("auth", per_minute=20))],
)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> TokenPair:
    try:
        user, new_refresh = await jwt_service.rotate_refresh_token(
            db,
            body.refresh_token,
            device_id=None,
            user_agent=request.headers.get("user-agent"),
        )
    except jwt_service.InvalidRefreshToken as e:
        log.warning("refresh_failed", reason=e.reason)
        raise HTTPException(status_code=401, detail="invalid_refresh_token") from None

    access, expires_in = jwt_service.create_access_token(user)
    return TokenPair(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=expires_in,
    )


# ─── Logout ──────────────────────────────────────────────────────────


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db_session),
    _user: User = Depends(current_user),
) -> None:
    if body.refresh_token:
        await jwt_service.revoke_refresh_token(db, body.refresh_token)


# ─── Helpers ─────────────────────────────────────────────────────────


async def _user_by_email(db: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email.lower())
    return (await db.execute(stmt)).scalar_one_or_none()
