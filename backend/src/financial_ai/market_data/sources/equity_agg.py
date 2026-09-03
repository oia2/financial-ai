"""Дневные агрегаты торгов по акциям.

Перенесено из `pipelines/iss_equity_aggregates_tqbr_sync/` (`MR-MASTER-DRO`,
`f07295e`). Тот же клиент и та же доска, что у котировок; отличается набор
колонок — оборот в деньгах, число сделок, средневзвешенная цена.

Как и котировки, ежедневный добор идёт **одним запросом по дате**.
"""

from __future__ import annotations

import datetime as dt
import logging

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import AggregateRow, MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import asset_id_for, price_series_id_for, to_decimal

logger = logging.getLogger(__name__)

SOURCE_ID = "equity_agg"
COLUMNS = ("SECID", "TRADEDATE", "VALUE", "NUMTRADES", "WAPRICE")


async def sync_equity_aggregates(
    client: IssClient, repository: MarketDataRepository, session_date: dt.date
) -> int:
    """Собрать агрегаты всех бумаг за одну торговую сессию."""
    rows = await client.fetch_session_rows(session_date.isoformat(), COLUMNS)
    aggregates = rows_to_aggregates(rows, session_date)
    written = await repository.upsert_aggregates(aggregates)
    logger.info("агрегаты за %s: получено %d, записано %d", session_date, len(rows), written)
    return written


def rows_to_aggregates(rows: list[dict[str, object]], session_date: dt.date) -> list[AggregateRow]:
    """Преобразовать ответ биржи в агрегаты.

    Пропуск остаётся пропуском: бумага могла не торговаться, и ноль оборота —
    это другое утверждение.
    """
    out: list[AggregateRow] = []
    seen: set[str] = set()

    for row in rows:
        secid = row.get("SECID")
        if not isinstance(secid, str) or not secid.strip():
            continue
        ticker = secid.strip().upper()
        if ticker in seen:
            continue
        seen.add(ticker)

        out.append(
            AggregateRow(
                asset_id=asset_id_for(ticker),
                price_series_id=price_series_id_for(ticker),
                session_date=session_date,
                value=to_decimal(row.get("VALUE")),
                num_trades=to_decimal(row.get("NUMTRADES")),
                waprice=to_decimal(row.get("WAPRICE")),
            )
        )
    return out
