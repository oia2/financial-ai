"""Источник дневных котировок акций.

Перенесён из `pipelines/iss_equity_prices_tqbr_sync/pipeline.py`
(`MR-MASTER-DRO`, `f07295e`) с двумя изменениями:

1. запись идёт в PostgreSQL вместо `data_2/raw/equity/*_D1.csv`;
2. **ежедневный добор ходит запросом по дате**, а не перебором бумаг.
   Оригинал строит адрес по тикеру: верно для первичной загрузки, но при
   ежедневном доборе даёт 288 обращений к бирже ради 288 строк.

Цены разбираются в ``Decimal``. ``float`` здесь не появляется ни на секунду:
пройдя через него, значение уже не восстановить.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

logger = logging.getLogger(__name__)

SOURCE_ID = "equity_d1"
COLUMNS = ("SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")

# Пока нет реестра непрерывности из исследовательского репозитория,
# идентификаторы строятся по тикеру — это законная форма якоря
# (pipelines/data_plane_step0/pipeline.py: ISIN, а при его отсутствии тикер).
ASSET_PREFIX = "EQ_AST_"
SERIES_PREFIX = "EQ_PRS_"


def asset_id_for(ticker: str) -> str:
    return f"{ASSET_PREFIX}{ticker.strip().upper()}"


def price_series_id_for(ticker: str) -> str:
    return f"{SERIES_PREFIX}{ticker.strip().upper()}"


async def sync_equity_daily(
    client: IssClient,
    repository: MarketDataRepository,
    session_date: dt.date,
) -> int:
    """Собрать котировки всех бумаг за одну торговую сессию.

    Одно обращение к бирже, а не одно на бумагу.
    """
    rows = await client.fetch_session_rows(session_date.isoformat(), COLUMNS)
    bars = rows_to_bars(rows, session_date)

    for bar in bars:
        ticker = bar.asset_id.removeprefix(ASSET_PREFIX)
        await repository.upsert_asset(bar.asset_id, ticker, session_date)
        await repository.upsert_price_series(bar.price_series_id, bar.asset_id, session_date)

    written = await repository.upsert_daily_bars(bars)
    logger.info("котировки за %s: получено строк %d, записано %d", session_date, len(rows), written)
    return written


def rows_to_bars(rows: list[dict[str, object]], session_date: dt.date) -> list[DailyBar]:
    """Преобразовать ответ биржи в наблюдения.

    Строка без тикера пропускается: она ни к чему не относится. Строка без
    цен сохраняется с ``None`` — отсутствие наблюдения это факт, а не ноль.
    """
    bars: list[DailyBar] = []
    seen: set[str] = set()

    for row in rows:
        secid = row.get("SECID")
        if not isinstance(secid, str) or not secid.strip():
            continue
        ticker = secid.strip().upper()
        if ticker in seen:
            # Дубли в пределах одной даты не должны порождать две строки:
            # ключ price_series_id + session_date обязан остаться ключом.
            logger.warning(
                "котировки за %s: повторная строка по %s пропущена", session_date, ticker
            )
            continue
        seen.add(ticker)

        bars.append(
            DailyBar(
                asset_id=asset_id_for(ticker),
                price_series_id=price_series_id_for(ticker),
                session_date=session_date,
                open=to_decimal(row.get("OPEN")),
                high=to_decimal(row.get("HIGH")),
                low=to_decimal(row.get("LOW")),
                close=to_decimal(row.get("CLOSE")),
                volume=to_decimal(row.get("VOLUME")),
            )
        )
    return bars


def to_decimal(raw: object) -> Decimal | None:
    """Разобрать значение в ``Decimal``.

    ``None`` означает «наблюдения нет» и НЕ заменяется нулём: отсутствие
    торгов и нулевая цена — разные факты, и модель обязана их различать.

    ``float`` на входе принимается, но переводится через ``str``: иначе
    значение биржи уже искажено к моменту записи.
    """
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        logger.warning("не удалось разобрать числовое значение %r", raw)
        return None
