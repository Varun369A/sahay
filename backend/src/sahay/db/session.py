"""Async SQLAlchemy engine + session factory.

Engine is module-level singleton (created once per process).
Use `get_db_session` as a FastAPI dependency to get an `AsyncSession`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sahay.config import get_settings

_settings = get_settings()

# asyncpg needs SSL passed via connect_args, NOT as a URL query param.
# config.py strips ssl/sslmode/channel_binding from the URL; we add it back here.
_connect_args: dict[str, object] = {}
if _settings.db_needs_ssl:
    _connect_args["ssl"] = True

# Engine pool tuning:
#   - pool_size 10 + max_overflow 20 → up to 30 concurrent DB conns per process
#   - pool_pre_ping detects dead conns (Neon scales-to-zero)
#   - pool_recycle 1800s prevents stale conns through proxies
engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
    connect_args=_connect_args,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager for non-FastAPI callers (background jobs, scripts)."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close the connection pool. Call on app shutdown."""
    await engine.dispose()
