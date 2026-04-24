"""user.role — role-aware agent persona

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-24

Adds a nullable `role` column to the users table. Populated by the user
during onboarding or from Settings ("I'm a student / engineer / founder /
doctor / sales / designer / teacher / other"). The agent injects this
value into Claude's system prompt each turn so recommendations, tone, and
document templates adapt to what the user actually does.

Nullable — existing users (and users who skip the picker) get a generic
"assistant" persona until they fill it in.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
