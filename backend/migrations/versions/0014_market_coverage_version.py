"""Версионировать подтверждённую полноту и закрепить границу старой истории.

Миграция не подтверждает старые исходы повторно. Для непустого сохранённого
календаря граница фиксируется один раз; для пустой базы строки границы нет и
первичная загрузка остаётся обычной работой.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "market_ingest_run",
        sa.Column("coverage_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "market_ingest_run",
        sa.Column("coverage_reason", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "market_coverage_boundary",
        sa.Column("coverage_version", sa.Integer(), primary_key=True),
        sa.Column("boundary_session", sa.Date(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        """
        INSERT INTO market_coverage_boundary (coverage_version, boundary_session)
        SELECT 1, MAX(session_date)
          FROM market_trading_session
        HAVING MAX(session_date) IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("market_coverage_boundary")
    op.drop_column("market_ingest_run", "coverage_reason")
    op.drop_column("market_ingest_run", "coverage_version")
