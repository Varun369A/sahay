"""user.mood — selectable assistant personality mode

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-24

Adds a nullable `mood` column to the users table. Mood tunes how the
agent TALKS — greeting style, humour, formality — independently of
`role` which tunes what it recommends. Null = "default" (balanced
professional + warm).
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mood", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "mood")
