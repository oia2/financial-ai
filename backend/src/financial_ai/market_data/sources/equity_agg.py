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
from financial_ai.market_data.sources.equity_d1 import (
    ASSET_PREFIX,
    asset_id_for,
    price_series_id_for,
    to_decimal,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "equity_agg"
COLUMNS = ("SECID", "TRADEDATE", "VALUE", "NUMTRADES", "WAPRICE")


async def sync_equity_aggregates(
    client: IssClient, repository: MarketDataRepository, session_date: dt.date
) -> int:
    """Собрать агрегаты всех бумаг за одну торговую сессию."""
    rows = await client.fetch_session_rows(session_date.isoformat(), COLUMNS)
    aliases = await repository.aliases_on(session_date)
    aggregates = rows_to_aggregates(rows, session_date, aliases)
    written = await repository.upsert_aggregates(aggregates)
    logger.info("агрегаты за %s: получено %d, записано %d", session_date, len(rows), written)
    return written


def rows_to_aggregates(
    rows: list[dict[str, object]],
    session_date: dt.date,
    aliases: dict[str, str] | None = None,
) -> list[AggregateRow]:
    """Преобразовать ответ биржи в агрегаты.

    Пропуск остаётся пропуском: бумага могла не торговаться, и ноль оборота —
    это другое утверждение.

    ``aliases`` отображает действующее имя бумаги на её сущность — тот же
    словарь, которым ключуются котировки. Без него переименование заводило
    вторую бумагу, и ряд оборотов рвался навсегда: котировки после
    переименования продолжались, агрегаты начинались заново (FR-048).
    """
    known = aliases or {}
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

        asset_id = known.get(ticker, asset_id_for(ticker))
        out.append(
            AggregateRow(
                asset_id=asset_id,
                price_series_id=price_series_id_for(asset_id.removeprefix(ASSET_PREFIX)),
                session_date=session_date,
                value=to_decimal(row.get("VALUE")),
                num_trades=to_decimal(row.get("NUMTRADES")),
                waprice=to_decimal(row.get("WAPRICE")),
            )
        )
    return out
