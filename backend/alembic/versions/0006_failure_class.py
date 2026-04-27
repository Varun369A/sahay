"""failure_class on playbook_run_outcomes — structured failure labels

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-27

Adds a nullable `failure_class` column to playbook_run_outcomes. Filled
by `services.failure_classifier.classify_from_reason` on every failure;
left NULL on success rows. Feeds the reliability dashboard and the
nightly Opus-driven improvement loop.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playbook_run_outcomes",
        sa.Column("failure_class", sa.String(length=32), nullable=True),
    )
    # Indexed because the dashboard groups by class:
    #   "what's the top failure class for app X this week?"
    op.create_index(
        "ix_playbook_run_outcomes_failure_class",
        "playbook_run_outcomes",
        ["failure_class"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playbook_run_outcomes_failure_class",
        table_name="playbook_run_outcomes",
    )
    op.drop_column("playbook_run_outcomes", "failure_class")
