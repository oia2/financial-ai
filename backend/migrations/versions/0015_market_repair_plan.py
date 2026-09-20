"""Persist manual market-data repair plans and their HTTP budget.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "market_repair_plan",
        sa.Column("plan_id", sa.String(length=36), primary_key=True),
        sa.Column("request_budget", sa.Integer(), nullable=False),
        sa.Column("requests_spent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "market_repair_item",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_id",
            sa.String(length=36),
            sa.ForeignKey("market_repair_plan.plan_id"),
            nullable=False,
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("expected_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("plan_id", "session_date", "source_id"),
    )


def downgrade() -> None:
    op.drop_table("market_repair_item")
    op.drop_table("market_repair_plan")
