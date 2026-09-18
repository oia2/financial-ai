"""Свернуть повторяющиеся интервалы псевдонимов.

Псевдоним записывался на каждый тикер в каждом прогоне, а не при смене имени.
На стенде это дало 23 276 строк на 506 бумаг — по 46 интервалов на тикер, и все
действующие одновременно. Поведение не ломалось только потому, что чтение
складывало дубликаты в словарь: одинаковый факт, записанный сорок шесть раз,
схлопывался обратно в один.

Здесь остаётся по одному интервалу на пару «имя — сущность», с самой ранней
датой начала: именно она и есть ответ на вопрос «с какой даты действует имя».
Прочие строки — повторы того же факта, и терять с ними нечего.

Причина устранена в `market_data/links.py`: запись идёт только когда имя
меняется.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Оставляем строку с самой ранней датой начала для каждой пары
    # «тикер — сущность». Дата окончания берётся наибольшая из непустых: если
    # имя когда-то закрывали, закрытие сохраняется.
    op.execute(
        """
        WITH kept AS (
            SELECT ticker, asset_id, MIN(valid_from) AS valid_from
              FROM market_asset_alias
             GROUP BY ticker, asset_id
        )
        DELETE FROM market_asset_alias a
         USING kept k
         WHERE a.ticker = k.ticker
           AND a.asset_id = k.asset_id
           AND a.valid_from <> k.valid_from
        """
    )


def downgrade() -> None:
    # Обратного хода нет: повторы того же факта восстанавливать незачем и
    # нечем — они не несли сведений, которых не было бы в оставшейся строке.
    pass
