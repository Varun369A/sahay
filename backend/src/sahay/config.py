"""Centralised configuration loaded from environment variables.

All env access goes through this module. No `os.environ[...]` elsewhere.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Runtime ─────────────────────────────────────────────────────
    env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    port: int = 8080

    # ─── Database ────────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description="Postgres URL (asyncpg driver). e.g. postgresql+asyncpg://user:pass@host/db",
    )

    # ─── Redis ───────────────────────────────────────────────────────
    redis_url: str = Field(..., description="Upstash Redis (TLS).")

    # ─── JWT ─────────────────────────────────────────────────────────
    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # ─── Resend ──────────────────────────────────────────────────────
    resend_api_key: SecretStr
    resend_from_email: str = "Sahay <noreply@mail.sahay.app>"
    resend_reply_to: str = "support@sahay.app"

    # ─── Anthropic ───────────────────────────────────────────────────
    anthropic_api_key: SecretStr
    claude_model_default: str = "claude-haiku-4-5"
    claude_model_quality: str = "claude-sonnet-4-6"
    claude_model_ingestion: str = "claude-opus-4-7"

    # ─── Cloudflare R2 ───────────────────────────────────────────────
    r2_account_id: str
    r2_access_key_id: SecretStr
    r2_secret_access_key: SecretStr
    r2_bucket: str = "sahay-files"
    r2_public_base_url: str = "https://files.sahay.app"

    # ─── Replicate ───────────────────────────────────────────────────
    replicate_api_token: SecretStr
    replicate_image_model: str = "black-forest-labs/flux-schnell"

    # ─── Razorpay (OPTIONAL — billing deferred) ──────────────────────
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None

    # ─── Sarvam ──────────────────────────────────────────────────────
    sarvam_api_key: SecretStr | None = None  # primarily used by Android client

    # ─── Sentry ──────────────────────────────────────────────────────
    sentry_dsn: str | None = None

    # ─── CORS ────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:3000,http://localhost:8080"

    # ─── Rate limits (per-user, per-minute) ──────────────────────────
    rate_limit_default: int = 60
    rate_limit_auth: int = 10

    # ─── Feature flags ───────────────────────────────────────────────
    enable_docs: bool = True
    enable_metrics: bool = True

    # ─── Computed / validators ───────────────────────────────────────
    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """Enforce asyncpg scheme and strip SSL query params.

        asyncpg doesn't accept `sslmode`/`ssl=require` as a query string —
        SSL is handled via `connect_args` in db/session.py instead. We strip
        these params here so Neon URLs (which include them) work unchanged.
        """
        # Scheme normalisation
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql:// or postgresql+asyncpg:// scheme")

        # Strip SSL-related query params — handled in session.py via connect_args.
        if "?" in v:
            base, qs = v.split("?", 1)
            pairs = [
                p for p in qs.split("&")
                if not p.lower().startswith(("sslmode=", "ssl=", "channel_binding="))
            ]
            v = f"{base}?{'&'.join(pairs)}" if pairs else base
        return v

    @property
    def db_needs_ssl(self) -> bool:
        """Infer whether the DB needs TLS (Neon, Supabase, RDS all do)."""
        url = self.database_url.lower()
        return any(h in url for h in ("neon.tech", "supabase", "amazonaws.com", "render.com")) \
            or "localhost" not in url and "127.0.0.1" not in url

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns a cached singleton. Use as a FastAPI dependency or directly."""
    return Settings()  # type: ignore[call-arg]
