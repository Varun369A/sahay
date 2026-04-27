"""playbook_revisions — versioned, candidate playbook specs

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-27

Backs the nightly Opus-driven improvement loop. Each row is one candidate
patched spec for an existing playbook. Status transitions:

    draft  →  canary  →  promoted   (success path)
                     →  rolled_back (failure path)

Promotion is performed by a separate scheduled task (next migration) that
reads the canary's success_count / fail_count and compares against the
parent. No RLS — this is shared library content.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbook_revisions",
        sa.Column("id", UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  primary_key=True, nullable=False),
        sa.Column("playbook_id", UUID(as_uuid=True),
                  sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec_json", JSONB(), nullable=False),
        sa.Column("created_by", sa.String(length=32),
                  nullable=False, server_default=sa.text("'opus_improver'")),
        sa.Column("status", sa.String(length=16),
                  nullable=False, server_default=sa.text("'draft'")),
        sa.Column("success_count", sa.Integer(),
                  nullable=False, server_default=sa.text("0")),
        sa.Column("fail_count", sa.Integer(),
                  nullable=False, server_default=sa.text("0")),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "playbook_id", "version", name="uq_playbook_revisions_pb_version",
        ),
        sa.CheckConstraint(
            "status IN ('draft','canary','promoted','rolled_back')",
            name="playbook_revisions_status_valid",
        ),
    )
    op.create_index(
        "ix_playbook_revisions_playbook_id",
        "playbook_revisions", ["playbook_id"],
    )
    op.create_index(
        "ix_playbook_revisions_status",
        "playbook_revisions", ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_playbook_revisions_status", table_name="playbook_revisions")
    op.drop_index("ix_playbook_revisions_playbook_id", table_name="playbook_revisions")
    op.drop_table("playbook_revisions")
