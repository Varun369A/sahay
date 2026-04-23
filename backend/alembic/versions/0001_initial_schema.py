"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-23

Creates all v1 tables. Mirrors src/sahay/db/models.py.
RLS policies live separately in infra/postgres-rls.sql and are
applied by hand at deploy time (not via alembic) to keep ops scripts
in one place.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Required Postgres extensions.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")    # case-insensitive emails

    # ── users ────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", CITEXT(), nullable=False, unique=True),
        sa.Column(
            "email_verified", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("display_name", sa.String(100)),
        sa.Column("locale", sa.String(8), nullable=False, server_default=sa.text("'en'")),
        sa.Column("tier", sa.String(16), nullable=False, server_default=sa.text("'free'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "tier IN ('free', 'pro', 'power', 'enterprise')",
            name="ck_users_tier_valid",
        ),
    )

    # ── otp_challenges ───────────────────────────────────────────────
    op.create_table(
        "otp_challenges",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", CITEXT(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("requested_ip", sa.String(45)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "purpose IN ('signup', 'login')",
            name="ck_otp_challenges_purpose_valid",
        ),
    )
    op.create_index("ix_otp_email_created", "otp_challenges", ["email", "created_at"])

    # ── refresh_tokens ───────────────────────────────────────────────
    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("device_id", sa.String(128)),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_refresh_user_active", "refresh_tokens", ["user_id", "revoked_at"]
    )

    # ── playbooks ────────────────────────────────────────────────────
    op.create_table(
        "playbooks",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("app_package", sa.String(255), nullable=False),
        sa.Column("app_name", sa.String(100), nullable=False),
        sa.Column("app_version", sa.String(32)),
        sa.Column("intent", sa.String(64), nullable=False),
        sa.Column("steps_json", JSONB(), nullable=False),
        sa.Column("source_doc_url", sa.Text()),
        sa.Column(
            "reliability_score", sa.Numeric(4, 3),
            nullable=False, server_default=sa.text("0.0"),
        ),
        sa.Column("popularity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "is_payment_app", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "app_package", "intent", "app_version",
            name="uq_playbooks_app_intent_version",
        ),
        sa.CheckConstraint(
            "reliability_score >= 0 AND reliability_score <= 1",
            name="ck_playbooks_reliability_in_range",
        ),
    )
    op.create_index("ix_playbooks_popularity", "playbooks", ["popularity"])

    # ── user_app_grants ──────────────────────────────────────────────
    op.create_table(
        "user_app_grants",
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("app_package", sa.String(255), primary_key=True),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )

    # ── action_logs ──────────────────────────────────────────────────
    op.create_table(
        "action_logs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("app_package", sa.String(255), nullable=False),
        sa.Column("intent", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("tokens_used", sa.Integer()),
        sa.Column("cost_inr_paise", sa.Integer()),
        sa.Column(
            "user_aborted", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("failure_reason", sa.String(255)),
        sa.Column("model_used", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed', 'aborted', 'needs_confirm')",
            name="ck_action_logs_status_valid",
        ),
    )
    op.create_index(
        "ix_action_logs_user_created", "action_logs", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_action_logs_app_intent", "action_logs", ["app_package", "intent"]
    )

    # ── usage_periods ────────────────────────────────────────────────
    op.create_table(
        "usage_periods",
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("period_type", sa.String(8), primary_key=True),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column(
            "action_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "tokens_used", sa.BigInteger(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_inr_paise", sa.BigInteger(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "image_gens", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.CheckConstraint(
            "period_type IN ('day', 'week', 'month')",
            name="ck_usage_periods_period_type_valid",
        ),
    )

    # ── files ────────────────────────────────────────────────────────
    op.create_table(
        "files",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("folder", sa.String(255), nullable=False, server_default=sa.text("'/'")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("r2_key", sa.String(512), nullable=False, unique=True),
        sa.Column(
            "parent_id", UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "source", sa.String(16),
            nullable=False, server_default=sa.text("'upload'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source IN ('upload', 'generated', 'edited')",
            name="ck_files_source_valid",
        ),
    )
    op.create_index("ix_files_user_folder", "files", ["user_id", "folder"])

    # ── subscriptions ────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("razorpay_sub_id", sa.String(64), unique=True),
        sa.Column("razorpay_customer_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'grace', 'cancelled', 'expired', 'pending')",
            name="ck_subscriptions_status_valid",
        ),
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_index("ix_files_user_folder", table_name="files")
    op.drop_table("files")
    op.drop_table("usage_periods")
    op.drop_index("ix_action_logs_app_intent", table_name="action_logs")
    op.drop_index("ix_action_logs_user_created", table_name="action_logs")
    op.drop_table("action_logs")
    op.drop_table("user_app_grants")
    op.drop_index("ix_playbooks_popularity", table_name="playbooks")
    op.drop_table("playbooks")
    op.drop_index("ix_refresh_user_active", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_otp_email_created", table_name="otp_challenges")
    op.drop_table("otp_challenges")
    op.drop_table("users")
