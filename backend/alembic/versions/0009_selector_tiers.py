"""playbook_run_outcomes.selector_tiers — per-step winning-tier telemetry

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-27

Stores the device's per-step record of which selector tier resolved the
match (`viewId` / `text_exact` / `cd_exact` / `text_substring` /
`cd_substring` / `className` / `sibling_anchor` / `resolver` /
`fuzzy_keyword` / `fuzzy_edittext`). Feeds the selector_trainer
aggregation that informs the nightly Opus improver — when a non-primary
tier wins consistently for a step, the primary selector should be
demoted in favour of the actual winner.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playbook_run_outcomes",
        sa.Column("selector_tiers", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playbook_run_outcomes", "selector_tiers")
