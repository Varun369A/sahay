"""FastAPI application entrypoint.

Wires: lifespan, middleware (CORS, request-id, error handler), Sentry,
health router. Feature routers (auth, playbooks, actions, files, billing)
are added in subsequent steps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import sentry_sdk
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from starlette.middleware.base import BaseHTTPMiddleware

from sahay import __version__
from sahay.config import get_settings
from sahay.db.session import dispose_engine
from sahay.routers import (
    actions,
    agent,
    auth,
    briefing,
    documents,
    files,
    health,
    memory,
    notifications,
    playbooks,
    users,
)
from sahay.util.logging import configure_logging, get_logger

settings = get_settings()


# ─── Lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup + shutdown hooks."""
    configure_logging()
    log = get_logger(__name__)

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.env,
            release=f"sahay-backend@{__version__}",
            traces_sample_rate=0.1 if settings.is_production else 0.0,
            profiles_sample_rate=0.1 if settings.is_production else 0.0,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            send_default_pii=False,
        )
        log.info("sentry_initialised")

    log.info(
        "sahay_starting",
        env=settings.env,
        version=__version__,
        port=settings.port,
    )

    yield

    log.info("sahay_shutting_down")
    await dispose_engine()


# ─── Middleware ──────────────────────────────────────────────────────


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique request_id to every request, propagate to logs + response header."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or str(uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()


# ─── App ─────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sahay API",
        version=__version__,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    # Request ID
    app.add_middleware(RequestIdMiddleware)

    # Routers
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(playbooks.router)
    app.include_router(actions.router)
    app.include_router(actions.usage_router)
    app.include_router(files.router)
    app.include_router(agent.router)
    app.include_router(memory.router)
    app.include_router(notifications.router)
    app.include_router(documents.router)
    app.include_router(briefing.router)

    # Global error handler — never leak stack traces in production.
    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> ORJSONResponse:
        log = get_logger(__name__)
        log.exception("unhandled_exception", path=request.url.path)
        if settings.is_production:
            return ORJSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "internal_server_error"},
            )
        return ORJSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_server_error", "detail": str(exc)},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> ORJSONResponse:
        return ORJSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "validation_error", "details": exc.errors()},
        )

    return app


app = create_app()
