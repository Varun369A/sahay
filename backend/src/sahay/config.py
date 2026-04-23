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

    # ─── Razorpay ────────────────────────────────────────────────────
    razorpay_key_id: str
    razorpay_key_secret: SecretStr
    razorpay_webhook_secret: SecretStr

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
    def _enforce_async_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            # Allow plain postgres:// but auto-convert.
            if v.startswith("postgres://"):
                v = v.replace("postgres://", "postgresql+asyncpg://", 1)
            elif v.startswith("postgresql://"):
                v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
            else:
                raise ValueError(
                    "DATABASE_URL must use postgresql+asyncpg:// scheme"
                )
        return v

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
