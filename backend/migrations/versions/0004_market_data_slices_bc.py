"""Агрегаты, позиции по фьючерсам и отраслевая принадлежность.

Срезы B и C сбора рыночных данных. Правила те же, что и в 0003: цены и
количества — NUMERIC(28,9), пропуск хранится как NULL и нулём не заменяется.

Про позиции отдельно: покрытие у них частичное по природе. NULL означает
«не знаем», а не «позиций нет» — для модели это разные утверждения.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICE = sa.Numeric(28, 9)


def upgrade() -> None:
    op.create_table(
        "market_equity_aggregate",
        sa.Column("price_series_id", sa.String(64), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("value", PRICE, nullable=True),
        sa.Column("num_trades", PRICE, nullable=True),
        sa.Column("waprice", PRICE, nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("price_series_id", "session_date"),
    )
    op.create_index("ix_equity_aggregate_session", "market_equity_aggregate", ["session_date"])

    op.create_table(
        "market_futures_position",
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        # NULL — «не знаем». Ноль означал бы «участники не держат позиций».
        sa.Column("fiz_long", PRICE, nullable=True),
        sa.Column("fiz_short", PRICE, nullable=True),
        sa.Column("jur_long", PRICE, nullable=True),
        sa.Column("jur_short", PRICE, nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id", "session_date"),
    )
    op.create_index("ix_futures_position_session", "market_futures_position", ["session_date"])

    op.create_table(
        "market_asset_sector",
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("sector", sa.String(128), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id"),
    )


def downgrade() -> None:
    op.drop_table("market_asset_sector")
    op.drop_table("market_futures_position")
    op.drop_table("market_equity_aggregate")
