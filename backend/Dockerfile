# syntax=docker/dockerfile:1.7
# Multi-stage build for slim production image.

ARG PYTHON_VERSION=3.12-slim-bookworm

# ─── Builder ────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip wheel \
    && pip wheel --no-deps --wheel-dir /wheels . \
    && pip wheel --wheel-dir /wheels .

# ─── Runtime ────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system sahay --gid 1001 \
    && useradd --system --uid 1001 --gid sahay --create-home --shell /bin/bash sahay

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels sahay-backend \
    && rm -rf /wheels

COPY --chown=sahay:sahay alembic.ini ./
COPY --chown=sahay:sahay alembic ./alembic

USER sahay

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1

# Run migrations then start server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn sahay.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
