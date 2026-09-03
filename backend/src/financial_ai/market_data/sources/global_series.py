"""Глобальные дневные ряды с MOEX ISS.

Индексы, курс USD и Brent приходят с одной биржи одним клиентом и различаются
только тремя вещами: раздел торгов, бумага и колонка со значением. Поэтому здесь
один загрузчик и объявление рядов, а не четыре почти одинаковых модуля.

Перенесено из `pipelines/iss_indices_close_value_sync/`,
`pipelines/usd000utstom_history_sync/` и `pipelines/br_continuous_history_sync/`
(`MR-MASTER-DRO`, `f07295e`): оттуда взяты разделы торгов, бумаги и колонки.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import to_decimal
from financial_ai.market_data.sources.trading_calendar import parse_date

logger = logging.getLogger(__name__)

SOURCE_ID = "global_series"


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """Объявление одного глобального ряда."""

    series_id: str
    secid: str
    engine: str
    market: str
    board: str | None = None
    value_column: str = "CLOSE"

    def columns(self) -> tuple[str, ...]:
        return ("SECID", "TRADEDATE", self.value_column)


# Разделы торгов взяты из конфигурации исходных пайплайнов, а не подобраны:
# индексы живут в engine=stock/market=index, валюта — в currency/selt.
ISS_SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("IMOEX", "IMOEX", engine="stock", market="index"),
    SeriesSpec("RTSI", "RTSI", engine="stock", market="index"),
    SeriesSpec("RGBI", "RGBI", engine="stock", market="index"),
    SeriesSpec("RVI", "RVI", engine="stock", market="index"),
    SeriesSpec("USD_ISS", "USD000UTSTOM", engine="currency", market="selt", board="CETS"),
)


async def sync_iss_series(
    client: IssClient,
    repository: MarketDataRepository,
    session_date: dt.date,
    specs: tuple[SeriesSpec, ...] = ISS_SERIES,
) -> int:
    """Собрать значения глобальных рядов за одну торговую сессию."""
    return await sync_iss_series_range(client, repository, session_date, session_date, specs)


async def sync_iss_series_range(
    client: IssClient,
    repository: MarketDataRepository,
    date_from: dt.date,
    date_till: dt.date,
    specs: tuple[SeriesSpec, ...] = ISS_SERIES,
) -> int:
    """Собрать значения глобальных рядов за период.

    Биржа отдаёт историю ряда диапазоном, и для догона это принципиально:
    дыра любой длины закрывается **одним** обращением на ряд вместо одного на
    каждую сессию. Ежедневный добор — частный случай с совпадающими границами.

    Неудача одного ряда не отменяет остальные: ряды независимы, и терять
    собранное из-за недоступности одного индекса незачем.
    """
    written = 0
    for spec in specs:
        values = await _fetch_series(client, spec, date_from, date_till)
        if values:
            written += await repository.upsert_global_values(spec.series_id, values)
    return written


async def _fetch_series(
    client: IssClient, spec: SeriesSpec, date_from: dt.date, date_till: dt.date
) -> dict[dt.date, Decimal | None]:
    try:
        rows = await client.fetch_security_history(
            spec.secid,
            date_from.isoformat(),
            date_till.isoformat(),
            spec.columns(),
            engine=spec.engine,
            market=spec.market,
            board=spec.board,
        )
    except Exception as error:  # noqa: BLE001 — один ряд не должен ронять остальные
        logger.warning("глобальный ряд %s не собран: %s", spec.series_id, error)
        return {}

    return rows_to_values(rows, spec.value_column)


def rows_to_values(
    rows: list[dict[str, object]], value_column: str
) -> dict[dt.date, Decimal | None]:
    """Преобразовать ответ биржи в значения по датам.

    Пропуск остаётся пропуском: отсутствие значения не заменяется нулём.
    """
    values: dict[dt.date, Decimal | None] = {}
    for row in rows:
        day = parse_date(row.get("TRADEDATE"))
        if day is None:
            continue
        values[day] = to_decimal(row.get(value_column))
    return values
