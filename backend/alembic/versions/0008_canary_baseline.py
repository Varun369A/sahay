"""playbook_revisions.parent_score_baseline — fair canary comparison

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-27

The parent playbook's `reliability_score` keeps moving while a canary
runs (non-canary traffic still updates it). To decide promote-vs-rollback
fairly, the canary needs to remember the parent's score AT THE MOMENT
the canary started — that's what this column captures.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playbook_revisions",
        sa.Column("parent_score_baseline", sa.Numeric(4, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playbook_revisions", "parent_score_baseline")
