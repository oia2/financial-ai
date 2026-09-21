"""Persist the exact per-session outcome of an ingest plan.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "market_ingest_session_outcome",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("session_date", sa.Date(), primary_key=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("selected_sources", sa.JSON(), nullable=False),
        sa.Column("interrupted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome IN ('collected', 'partial', 'failed', 'skipped', 'pending')",
            name="ck_ingest_session_outcome_value",
        ),
    )
    op.create_index(
        "ix_ingest_session_outcome_run",
        "market_ingest_session_outcome",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_session_outcome_run", table_name="market_ingest_session_outcome")
    op.drop_table("market_ingest_session_outcome")
