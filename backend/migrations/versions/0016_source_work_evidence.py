"""Persist verified market-source work and start coverage rule version 2.

Old observations, ingest outcomes, and the version-1 boundary remain unchanged.
The migration records a version-2 boundary only for a non-empty calendar and
does not infer evidence, call external services, or start repair work.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "market_source_work_evidence",
        sa.Column("source_id", sa.String(length=64), primary_key=True),
        sa.Column("session_date", sa.Date(), primary_key=True),
        sa.Column("work_key", sa.String(length=192), primary_key=True),
        sa.Column("coverage_version", sa.Integer(), primary_key=True),
        sa.Column("result_kind", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("origin_run_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "result_kind IN ('value', 'confirmed_absence', 'not_applicable')",
            name="ck_source_work_evidence_result_kind",
        ),
    )
    op.create_index(
        "ix_source_work_evidence_session",
        "market_source_work_evidence",
        ["session_date"],
    )
    op.execute(
        """
        INSERT INTO market_coverage_boundary (coverage_version, boundary_session)
        SELECT 2, MAX(session_date)
          FROM market_trading_session
        HAVING MAX(session_date) IS NOT NULL
        ON CONFLICT (coverage_version) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM market_coverage_boundary WHERE coverage_version = 2")
    op.drop_index("ix_source_work_evidence_session", table_name="market_source_work_evidence")
    op.drop_table("market_source_work_evidence")
