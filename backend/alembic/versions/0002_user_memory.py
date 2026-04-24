"""user_memory — long-term agent memory per user

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-24

Adds the user_memory table plus indexes and RLS policy.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memory",
        sa.Column("id", UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  primary_key=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32),
                  nullable=False, server_default=sa.text("'facts'")),
        sa.Column("importance", sa.Integer(),
                  nullable=False, server_default=sa.text("5")),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("source", sa.String(length=16),
                  nullable=False, server_default=sa.text("'agent'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "key", name="uq_user_memory_user_key"),
        sa.CheckConstraint(
            "importance BETWEEN 1 AND 10",
            name="ck_user_memory_importance_range",
        ),
    )
    op.create_index("ix_user_memory_user_id", "user_memory", ["user_id"])
    op.create_index(
        "ix_user_memory_user_importance", "user_memory",
        ["user_id", "importance"],
    )
    op.create_index(
        "ix_user_memory_user_last_accessed", "user_memory",
        ["user_id", "last_accessed_at"],
    )

    # Row-level security — users see only their own memories.
    op.execute("ALTER TABLE user_memory ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY user_memory_tenant_isolation ON user_memory
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_memory_tenant_isolation ON user_memory")
    op.drop_index("ix_user_memory_user_last_accessed", table_name="user_memory")
    op.drop_index("ix_user_memory_user_importance", table_name="user_memory")
    op.drop_index("ix_user_memory_user_id", table_name="user_memory")
    op.drop_table("user_memory")
