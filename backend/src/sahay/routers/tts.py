"""Premium TTS endpoint — proxies ElevenLabs streaming synthesis.

POST /v1/tts/speak       — stream MP3 back to the device
GET  /v1/tts/budget      — return the caller's remaining char budget

The key stays on the server. The device never sees it. This also lets
us enforce per-user monthly character budgets (Redis) and swap providers
later without changing the Android client.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sahay.db.models import User
from sahay.deps import current_user
from sahay.services import elevenlabs_client
from sahay.services.rate_limiter import rate_limit
from sahay.util.logging import get_logger

router = APIRouter(prefix="/v1/tts", tags=["tts"])
log = get_logger(__name__)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    voice_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=64)


class BudgetResponse(BaseModel):
    used_chars: int
    limit_chars: int
    remaining_chars: int


@router.post(
    "/speak",
    dependencies=[Depends(rate_limit("tts_speak", per_minute=40))],
    responses={
        200: {"content": {"audio/mpeg": {}}},
        402: {"description": "monthly character budget exceeded"},
        503: {"description": "TTS provider unavailable"},
    },
)
async def speak(
    body: SpeakRequest,
    user: User = Depends(current_user),
) -> StreamingResponse:
    try:
        stream = elevenlabs_client.stream_speech(
            user_id=str(user.id),
            tier=user.tier,
            text=body.text,
            voice_id=body.voice_id,
            model_id=body.model_id,
        )
    except elevenlabs_client.TtsUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"tts_unavailable: {e}",
        ) from e

    # The generator will raise TtsBudgetExceeded / TtsUnavailable on first
    # iteration (before yielding), so we need a thin wrapper that turns
    # those into a clean HTTP error.
    async def _body():
        try:
            async for chunk in stream:
                yield chunk
        except elevenlabs_client.TtsBudgetExceeded as e:
            # Too late to change the status code — log and stop the stream.
            # Clients should check GET /v1/tts/budget before long utterances.
            log.warning("tts_budget_mid_stream", err=str(e))
            return
        except elevenlabs_client.TtsUnavailable as e:
            log.warning("tts_unavailable_mid_stream", err=str(e))
            return

    headers = {
        # Tell the client this is chunked audio, not a download.
        "Content-Type": "audio/mpeg",
        "Cache-Control": "no-store",
        # Hint to Android's MediaPlayer that we're streaming.
        "Accept-Ranges": "none",
    }

    # Pre-flight the budget so 402 can be returned cleanly before we open
    # a chunked response. We repeat the check inside stream_speech for
    # safety but this gives callers a meaningful HTTP status.
    try:
        budget = await elevenlabs_client.current_budget(
            user_id=str(user.id), tier=user.tier,
        )
        if len(body.text) > budget.remaining:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "tts_budget_exceeded",
                    "used_chars": budget.used_chars,
                    "limit_chars": budget.limit_chars,
                    "need_chars": len(body.text),
                },
            )
    except elevenlabs_client.TtsUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"tts_unavailable: {e}",
        ) from e

    return StreamingResponse(_body(), media_type="audio/mpeg", headers=headers)


@router.get("/budget", response_model=BudgetResponse,
            dependencies=[Depends(rate_limit("tts_budget", per_minute=30))])
async def budget(user: User = Depends(current_user)) -> BudgetResponse:
    try:
        b = await elevenlabs_client.current_budget(
            user_id=str(user.id), tier=user.tier,
        )
    except elevenlabs_client.TtsUnavailable:
        # Unconfigured provider → treat as no budget (client should use local TTS).
        return BudgetResponse(used_chars=0, limit_chars=0, remaining_chars=0)
    return BudgetResponse(
        used_chars=b.used_chars,
        limit_chars=b.limit_chars,
        remaining_chars=b.remaining,
    )
