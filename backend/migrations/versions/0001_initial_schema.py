"""Начальная схема: счёт, состояние, позиции, статус синхронизации, настройка обновления.

Revision ID: 0001
Revises:
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# NUMERIC(28,9): nano-точность T-Invest сохраняется без потерь (SC-002).
MONEY = sa.Numeric(28, 9)


def upgrade() -> None:
    op.create_table(
        "investment_account",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("broker_account_id", sa.Text(), nullable=False),
        sa.Column("masked_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_investment_account_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "account_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("account_id", sa.SmallInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_value", MONEY, nullable=False),
        sa.Column("cash", MONEY, nullable=False),
        sa.Column("positions_cost_basis", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=False),
        sa.Column("positions_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_account_state_singleton"),
        sa.CheckConstraint("positions_count >= 0", name="ck_account_state_positions_count"),
        sa.ForeignKeyConstraint(["account_id"], ["investment_account.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "portfolio_position",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("state_id", sa.SmallInteger(), nullable=False),
        sa.Column("instrument_uid", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("asset_type", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("average_price", MONEY, nullable=True),
        sa.Column("current_price", MONEY, nullable=False),
        sa.Column("value", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["state_id"], ["account_state.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_position_state", "portfolio_position", ["state_id"])

    op.create_table(
        "broker_sync_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("broker_status", sa.Text(), server_default="not_configured", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.Text(), server_default="failed", nullable=False),
        sa.Column("failure_reason_code", sa.Text(), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_broker_sync_state_singleton"),
        sa.CheckConstraint(
            "broker_status in ('connected', 'not_configured', 'rejected')",
            name="ck_broker_sync_state_status",
        ),
        sa.CheckConstraint("last_status in ('ok', 'failed')", name="ck_broker_sync_last_status"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "account_refresh_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_refresh_settings_singleton"),
        sa.CheckConstraint(
            "interval_seconds between 15 and 3600", name="ck_refresh_settings_interval_range"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Система работоспособна без единого обращения пользователя к настройке:
    # интервал по умолчанию 60 секунд, статус брокера — «не сконфигурирован»
    # до первой успешной синхронизации.
    op.execute("insert into account_refresh_settings (id, interval_seconds) values (1, 60)")
    op.execute(
        "insert into broker_sync_state (id, broker_status, last_status, consecutive_failures) "
        "values (1, 'not_configured', 'failed', 0)"
    )


def downgrade() -> None:
    op.drop_table("account_refresh_settings")
    op.drop_table("broker_sync_state")
    op.drop_index("idx_position_state", table_name="portfolio_position")
    op.drop_table("portfolio_position")
    op.drop_table("account_state")
    op.drop_table("investment_account")
