"""Дивидендные события.

Единственный источник не с биржи, а от брокера. Ключ — актив плюс дата фиксации
реестра: по ней событие однозначно.

Сумма — NUMERIC(28,9), как и остальные денежные величины: брокер отдаёт деньги
парой «целые + нано», и складывать их через float нельзя.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_dividend_event",
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("declared_date", sa.Date(), nullable=True),
        sa.Column("last_buy_date", sa.Date(), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        # NULL — «дивиденд не объявлен». Ноль означал бы «объявлен нулевым».
        sa.Column("value", sa.Numeric(28, 9), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id", "record_date"),
    )
    op.create_index("ix_dividend_event_asset", "market_dividend_event", ["asset_id"])


def downgrade() -> None:
    op.drop_table("market_dividend_event")
