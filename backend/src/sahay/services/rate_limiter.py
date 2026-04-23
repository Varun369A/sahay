"""Redis-backed sliding-window rate limiter.

Use as a dependency: `Depends(rate_limit("auth", per_minute=10))`.
Keyed by IP for unauthenticated routes, by user_id for authenticated.
"""

from __future__ import annotations

from collections.abc import Callable
from time import time

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status

from sahay.config import get_settings
from sahay.deps import client_ip
from sahay.util.logging import get_logger

settings = get_settings()
log = get_logger(__name__)

# Module-level singleton — Redis client is fork-safe.
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            retry_on_timeout=True,
        )
    return _redis


async def _check_and_incr(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Sliding window via fixed buckets (cheap + good enough).

    Returns (allowed, count_after_incr).
    """
    r = get_redis()
    bucket = int(time() // window_seconds)
    full_key = f"rl:{key}:{bucket}"
    pipe = r.pipeline()
    pipe.incr(full_key, 1)
    pipe.expire(full_key, window_seconds + 5)
    count, _ = await pipe.execute()
    return (count <= limit, int(count))


def rate_limit(scope: str, per_minute: int | None = None) -> Callable:
    """Dependency factory.

    `scope` namespaces the limiter (e.g. 'auth', 'actions').
    `per_minute` overrides settings; defaults to settings.rate_limit_default.
    """
    limit = per_minute if per_minute is not None else settings.rate_limit_default

    async def _dep(request: Request) -> None:
        # Prefer authenticated user_id for keying, else fall back to IP.
        user_id = getattr(request.state, "user_id", None)
        identifier = str(user_id) if user_id else (client_ip(request) or "anon")
        key = f"{scope}:{identifier}"
        allowed, count = await _check_and_incr(key, limit, window_seconds=60)
        if not allowed:
            log.warning("rate_limit_exceeded", scope=scope, identifier=identifier, count=count)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limit_exceeded",
                headers={"Retry-After": "60"},
            )

    return _dep


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
