"""Справочные источники: отраслевая принадлежность и состав индексов.

В отличие от рыночных рядов, справочники меняются редко и не привязаны к
торговой сессии. Собирать их каждый вечер незачем — достаточно обновлять
вместе с остальным и хранить последнее известное значение.

Перенесено из `pipelines/iss_equity_sector_sync/` и
`pipelines/iss_index_constituents_daily_sync/` (`MR-MASTER-DRO`, `f07295e`) в
части получения и разбора.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import asset_id_for, to_decimal
from financial_ai.market_data.sources.trading_calendar import parse_date

logger = logging.getLogger(__name__)

SECTORS_SOURCE_ID = "equity_sectors"
CONSTITUENTS_SOURCE_ID = "index_constituents"

SECTOR_COLUMNS = ("SECID", "SECTORID", "SECTORNAME")
CONSTITUENT_COLUMNS = ("SECID", "TRADEDATE", "WEIGHT")

# Вес бумаги в индексе — глобальный ряд на актив: имя ряда несёт индекс и бумагу.
INDEX_WEIGHT_PREFIX = "IDX_WEIGHT_"


async def sync_sectors(
    client: IssClient, repository: MarketDataRepository, session_date: dt.date
) -> int:
    """Обновить отраслевую принадлежность эмитентов."""
    rows = await client.fetch_session_rows(session_date.isoformat(), SECTOR_COLUMNS)
    written = await repository.upsert_sectors(rows_to_sectors(rows))
    logger.info("секторы: получено %d, записано %d", len(rows), written)
    return written


def rows_to_sectors(rows: list[dict[str, object]]) -> dict[str, str | None]:
    """Преобразовать ответ биржи в справочник отраслей.

    Отсутствие отрасли остаётся ``None``: «отрасль неизвестна» и «отрасль
    отсутствует» для справочника одно и то же, но подставлять сюда строку-
    заглушку нельзя — она попала бы в признаки как настоящая категория.
    """
    sectors: dict[str, str | None] = {}
    for row in rows:
        secid = row.get("SECID")
        if not isinstance(secid, str) or not secid.strip():
            continue
        name = row.get("SECTORNAME") or row.get("SECTORID")
        sectors[asset_id_for(secid.strip().upper())] = (
            str(name).strip() if isinstance(name, str) and name.strip() else None
        )
    return sectors


async def sync_index_constituents(
    client: IssClient,
    repository: MarketDataRepository,
    session_date: dt.date,
    index_id: str = "IMOEX",
) -> int:
    """Собрать дневной состав индекса и веса бумаг."""
    rows = await client.fetch_session_rows(session_date.isoformat(), CONSTITUENT_COLUMNS)
    written = 0
    for series_id, values in rows_to_weights(rows, session_date, index_id).items():
        written += await repository.upsert_global_values(series_id, values)
    logger.info("состав индекса %s за %s: рядов %d", index_id, session_date, written)
    return written


def rows_to_weights(
    rows: list[dict[str, object]], session_date: dt.date, index_id: str
) -> dict[str, dict[dt.date, Decimal | None]]:
    """Веса бумаг в индексе как отдельные ряды.

    Отсутствие бумаги в составе — не нулевой вес, а отсутствие ряда на эту
    дату: иначе выбывшая из индекса бумага выглядела бы как бумага с нулевым
    весом, что для модели другое утверждение.
    """
    out: dict[str, dict[dt.date, Decimal | None]] = {}
    for row in rows:
        secid = row.get("SECID")
        if not isinstance(secid, str) or not secid.strip():
            continue
        day = parse_date(row.get("TRADEDATE")) or session_date
        series_id = f"{INDEX_WEIGHT_PREFIX}{index_id}_{secid.strip().upper()}"
        out.setdefault(series_id, {})[day] = to_decimal(row.get("WEIGHT"))
    return out
