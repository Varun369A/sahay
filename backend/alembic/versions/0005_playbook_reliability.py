"""playbook reliability — Bayesian counters + per-run outcome telemetry

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-27

Wires the previously-dead `playbooks.reliability_score` field with a real
closed loop:

  • Adds `success_count`, `fail_count`, `expect_fail_count` to playbooks.
  • Creates `playbook_run_outcomes` (one row per run end), RLS-isolated to
    the user that produced it.

`reliability_score` itself is recomputed by the application service on
every outcome write as (success + 1) / (success + fail + 2). New
playbooks therefore start at 0.5 and converge to the true success rate.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── playbooks: Bayesian counter columns ──────────────────────────
    op.add_column(
        "playbooks",
        sa.Column(
            "success_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "playbooks",
        sa.Column(
            "fail_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "playbooks",
        sa.Column(
            "expect_fail_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "reliability_counts_sane",
        "playbooks",
        "success_count >= 0 AND fail_count >= 0 AND expect_fail_count >= 0 "
        "AND expect_fail_count <= fail_count",
    )

    # ── playbook_run_outcomes: per-run telemetry ─────────────────────
    op.create_table(
        "playbook_run_outcomes",
        sa.Column("id", UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  primary_key=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("playbook_id", UUID(as_uuid=True),
                  sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("failed_step_index", sa.Integer(), nullable=True),
        sa.Column("failed_reason", sa.String(length=200), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(),
                  nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_playbook_run_outcomes_playbook_id",
        "playbook_run_outcomes", ["playbook_id"],
    )
    op.create_index(
        "ix_playbook_run_outcomes_user_id",
        "playbook_run_outcomes", ["user_id"],
    )

    # RLS — users only see their own outcome rows. The aggregate counters
    # on playbooks themselves are public (the playbook library is shared).
    op.execute("ALTER TABLE playbook_run_outcomes ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY playbook_run_outcomes_tenant_isolation ON playbook_run_outcomes
        USING (user_id = current_setting('app.user_id', true)::uuid)
        WITH CHECK (user_id = current_setting('app.user_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS playbook_run_outcomes_tenant_isolation "
        "ON playbook_run_outcomes"
    )
    op.drop_index(
        "ix_playbook_run_outcomes_user_id", table_name="playbook_run_outcomes"
    )
    op.drop_index(
        "ix_playbook_run_outcomes_playbook_id", table_name="playbook_run_outcomes"
    )
    op.drop_table("playbook_run_outcomes")

    op.drop_constraint("reliability_counts_sane", "playbooks", type_="check")
    op.drop_column("playbooks", "expect_fail_count")
    op.drop_column("playbooks", "fail_count")
    op.drop_column("playbooks", "success_count")
