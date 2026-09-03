"""Схема рыночных данных MOEX.

Торговые сессии, активы, ценовые ряды, дневные наблюдения, глобальные ряды и
результаты прогонов сбора — по specs/003-moex-data-ingestion/data-model.md.

Цены — NUMERIC(28,9), как и денежные величины: float на пути «биржа → БД →
набор» запрещён. Пропуск хранится как NULL и нулём не заменяется.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICE = sa.Numeric(28, 9)


def upgrade() -> None:
    op.create_table(
        "market_trading_session",
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("session_date"),
    )

    op.create_table(
        "market_asset",
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("first_seen_date", sa.Date(), nullable=True),
        sa.Column("last_seen_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("asset_id"),
    )
    op.create_index("ix_market_asset_ticker", "market_asset", ["ticker"])

    op.create_table(
        "market_price_series",
        sa.Column("price_series_id", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["market_asset.asset_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("price_series_id"),
    )
    op.create_index("ix_market_price_series_asset_id", "market_price_series", ["asset_id"])

    op.create_table(
        "market_equity_daily_bar",
        sa.Column("price_series_id", sa.String(64), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        # NULL — «наблюдения нет». Нулём не заменяется.
        sa.Column("open", PRICE, nullable=True),
        sa.Column("high", PRICE, nullable=True),
        sa.Column("low", PRICE, nullable=True),
        sa.Column("close", PRICE, nullable=True),
        sa.Column("volume", PRICE, nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("source_revision", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("price_series_id", "session_date"),
    )
    op.create_index("ix_equity_daily_bar_session", "market_equity_daily_bar", ["session_date"])
    op.create_index(
        "ix_equity_daily_bar_asset_session", "market_equity_daily_bar", ["asset_id", "session_date"]
    )

    op.create_table(
        "market_global_daily_series",
        sa.Column("series_id", sa.String(64), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("value", PRICE, nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("series_id", "session_date"),
    )

    op.create_table(
        "market_ingest_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("rows_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_id", name="uq_ingest_run_source"),
    )
    op.create_index("ix_market_ingest_run_run_id", "market_ingest_run", ["run_id"])
    op.create_index("ix_ingest_run_session", "market_ingest_run", ["session_date"])


def downgrade() -> None:
    op.drop_table("market_ingest_run")
    op.drop_table("market_global_daily_series")
    op.drop_table("market_equity_daily_bar")
    op.drop_table("market_price_series")
    op.drop_table("market_asset")
    op.drop_table("market_trading_session")
