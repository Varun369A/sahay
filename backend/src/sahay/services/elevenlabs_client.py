"""ElevenLabs streaming TTS client.

Flow
────
Android wants Sahay's voice to sound human, not robotic. We proxy TTS
through our backend (never expose the ElevenLabs key on-device) using
ElevenLabs' streaming endpoint, then pipe MP3 chunks straight to the
client as they arrive. Time-to-first-byte is ~250 ms with the Turbo v2.5
model — well under the threshold where a user starts to feel the app
is thinking.

Cost posture
────────────
ElevenLabs bills in characters synthesised (not the number of API
calls). We enforce a per-user monthly character budget in Redis so a
bug in the agent can't silently burn through the account. Free users
get a small budget; Pro users get bumped.

Fallback
────────
If ElevenLabs is unreachable, returns 503 `tts_unavailable`. The Android
client is expected to fall back to on-device Android TTS on that error
so the user still hears something. That degrade is by design — premium
voice is a nice-to-have, not a correctness requirement.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from sahay.config import get_settings
from sahay.services.rate_limiter import get_redis
from sahay.util.logging import get_logger

log = get_logger(__name__)
settings = get_settings()


_STREAM_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
# The streaming endpoint emits MP3 chunks we forward to the client.
_OUTPUT_FORMAT = "mp3_44100_128"


@dataclass(slots=True)
class Budget:
    used_chars: int
    limit_chars: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit_chars - self.used_chars)


class TtsUnavailable(Exception):
    """Raised when ElevenLabs isn't configured or is unreachable."""


class TtsBudgetExceeded(Exception):
    """Raised when the user has already spent their monthly char budget."""


# ─── Budget tracking (Redis) ─────────────────────────────────────────


def _month_key(user_id: str) -> str:
    # Fixed-bucket monthly keying — no sliding window needed, billing is
    # calendar-month anyway.
    import time
    ym = time.strftime("%Y%m", time.gmtime())
    return f"tts_chars:{ym}:{user_id}"


async def _get_budget(user_id: str, tier: str) -> Budget:
    r = get_redis()
    raw = await r.get(_month_key(user_id))
    used = int(raw) if raw else 0
    limit = (
        settings.elevenlabs_monthly_char_budget_pro
        if tier.lower() in {"pro", "power", "enterprise"}
        else settings.elevenlabs_monthly_char_budget_free
    )
    return Budget(used_chars=used, limit_chars=limit)


async def _charge(user_id: str, chars: int) -> None:
    """Increment the user's monthly char counter. TTL gives us auto-cleanup
    of buckets that the user never touches again."""
    r = get_redis()
    key = _month_key(user_id)
    pipe = r.pipeline()
    pipe.incrby(key, chars)
    pipe.expire(key, 40 * 24 * 3600)       # 40 days — survives month boundary
    await pipe.execute()


# ─── Streaming synthesis ─────────────────────────────────────────────


async def stream_speech(
    *,
    user_id: str,
    tier: str,
    text: str,
    voice_id: str | None = None,
    model_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Yield MP3 chunks from ElevenLabs, or raise.

    Checks the budget BEFORE dialing the provider so we never pay for
    audio we're not going to charge the user for. Charges the full
    character count after the first byte arrives — ElevenLabs bills on
    submission, not on bytes-received, and we want matching book-keeping.
    """
    if settings.elevenlabs_api_key is None:
        raise TtsUnavailable("elevenlabs_not_configured")

    chars = len(text)
    if chars == 0:
        raise TtsUnavailable("empty_text")

    budget = await _get_budget(user_id, tier)
    if chars > budget.remaining:
        log.warning(
            "tts_budget_exceeded",
            user_id=user_id, chars=chars,
            used=budget.used_chars, limit=budget.limit_chars,
        )
        raise TtsBudgetExceeded(
            f"monthly char budget exceeded "
            f"({budget.used_chars}/{budget.limit_chars})"
        )

    vid = voice_id or settings.elevenlabs_default_voice_id
    mid = model_id or settings.elevenlabs_default_model_id

    url = _STREAM_URL.format(voice_id=vid)
    headers = {
        "xi-api-key": settings.elevenlabs_api_key.get_secret_value(),
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "text": text,
        "model_id": mid,
        "output_format": _OUTPUT_FORMAT,
        # Voice settings tuned for calm narration — stable + slight expression,
        # avoids the robotic-flat or over-theatrical extremes.
        "voice_settings": {
            "stability": 0.55,
            "similarity_boost": 0.7,
            "style": 0.15,
            "use_speaker_boost": True,
        },
    }

    # Retry only on connect / transient network errors. HTTP 4xx is fatal.
    charged = False
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(2),
        wait=wait_exponential_jitter(initial=0.3, max=1.0),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout)),
        reraise=True,
    ):
        with attempt:
            client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5))
            try:
                req = client.build_request("POST", url, json=body, headers=headers)
                resp = await client.send(req, stream=True)
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode("utf-8", errors="replace")[:200]
                    await resp.aclose()
                    await client.aclose()
                    log.error(
                        "elevenlabs_http_error",
                        status=resp.status_code, msg=err_text,
                    )
                    raise TtsUnavailable(
                        f"elevenlabs_status_{resp.status_code}: {err_text}"
                    )

                # First successful response → charge the user (bytes will flow next).
                if not charged:
                    await _charge(user_id, chars)
                    charged = True
                    log.info(
                        "tts_streaming",
                        user_id=user_id, chars=chars,
                        remaining_after=budget.remaining - chars,
                        voice_id=vid, model_id=mid,
                    )

                try:
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        if chunk:
                            yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()
                return
            except httpx.HTTPError:
                await client.aclose()
                raise


async def current_budget(user_id: str, tier: str) -> Budget:
    """Expose budget for the /v1/tts/budget endpoint (client UI)."""
    return await _get_budget(user_id, tier)
