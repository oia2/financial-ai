"""Связи инструментов во времени, причины пропусков и период исхода источника.

Четыре изменения одной миграцией, потому что их вводит одна фича (spec 008).

**Связь бумаги и фьючерса становится историей.** Соответствие строилось из ISS на
каждый прогон и жило в памяти процесса: ответить «с какой даты у бумаги есть
фьючерс» было нельзя, а смена семейства контрактов проходила бесследно.

**Контракт входит в ключ позиций.** Без него повторный сбор той же даты другим
семейством молча затирал бы прежнее наблюдение, и ряд склеивался бы из двух
инструментов без следа. Существующие строки получают ``unknown``: связей на
момент миграции ещё нет, и выдавать их за собранные по новым правилам нельзя.

**Причина пропуска переживает перезапуск.** Ход прогона живёт в памяти намеренно,
а причина обязана жить дольше: без неё человек видит дыру и не знает, ждать ему
или вмешиваться.

**У исхода источника появляется период.** Источник с выборкой за диапазон
записывает исход на конец периода; без периода он выглядит несобравшим всё,
кроме последней сессии. Существующие строки получают период в одну сессию — это
верно для всех посессионных источников и не хуже прежнего для диапазонных.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_asset_futures_link",
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("contract_code", sa.String(length=32), nullable=False),
        sa.Column("valid_till", sa.Date(), nullable=True),
        sa.Column("chosen_by", sa.String(length=32), nullable=False),
        sa.Column("open_interest", sa.Integer(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id", "valid_from"),
    )
    op.create_index(
        "ix_asset_futures_link_asset", "market_asset_futures_link", ["asset_id"], unique=False
    )

    op.create_table(
        "market_asset_alias",
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("valid_till", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("ticker", "valid_from"),
    )
    op.create_index("ix_asset_alias_asset", "market_asset_alias", ["asset_id"], unique=False)

    op.create_table(
        "market_session_skip",
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("session_date", "decided_at"),
    )
    op.create_index("ix_session_skip_session", "market_session_skip", ["session_date"], unique=False)

    op.add_column("market_asset", sa.Column("isin", sa.String(length=12), nullable=True))
    op.create_index("ix_market_asset_isin", "market_asset", ["isin"], unique=False)

    # Контракт в ключе позиций: сначала колонка со значением для уже собранного,
    # затем перекладка первичного ключа.
    op.add_column(
        "market_futures_position", sa.Column("contract_code", sa.String(length=32), nullable=True)
    )
    op.execute("UPDATE market_futures_position SET contract_code = 'unknown'")
    op.alter_column("market_futures_position", "contract_code", nullable=False)
    op.drop_constraint("market_futures_position_pkey", "market_futures_position", type_="primary")
    op.create_primary_key(
        "market_futures_position_pkey",
        "market_futures_position",
        ["asset_id", "session_date", "contract_code"],
    )

    op.add_column("market_ingest_run", sa.Column("period_from", sa.Date(), nullable=True))
    op.add_column("market_ingest_run", sa.Column("period_till", sa.Date(), nullable=True))
    op.execute(
        "UPDATE market_ingest_run "
        "SET period_from = session_date, period_till = session_date "
        "WHERE session_date IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("market_ingest_run", "period_till")
    op.drop_column("market_ingest_run", "period_from")

    op.drop_constraint("market_futures_position_pkey", "market_futures_position", type_="primary")
    op.create_primary_key(
        "market_futures_position_pkey", "market_futures_position", ["asset_id", "session_date"]
    )
    op.drop_column("market_futures_position", "contract_code")

    op.drop_index("ix_market_asset_isin", table_name="market_asset")
    op.drop_column("market_asset", "isin")

    op.drop_index("ix_session_skip_session", table_name="market_session_skip")
    op.drop_table("market_session_skip")

    op.drop_index("ix_asset_alias_asset", table_name="market_asset_alias")
    op.drop_table("market_asset_alias")

    op.drop_index("ix_asset_futures_link_asset", table_name="market_asset_futures_link")
    op.drop_table("market_asset_futures_link")
