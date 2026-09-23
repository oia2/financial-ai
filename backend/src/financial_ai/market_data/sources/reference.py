"""Справочные источники: отраслевая принадлежность и состав индексов.

В отличие от рыночных рядов, справочники меняются редко и не привязаны к
торговой сессии. Собирать их каждый вечер незачем — достаточно обновлять
вместе с остальным и хранить последнее известное значение.

**Оба источника берутся из раздела аналитики индексов, а не из истории торгов.**
Это не деталь адреса, а разные данные. Прежняя версия спрашивала историю
торгов TQBR и просила у неё колонки `WEIGHT`, `SECTORID`, `SECTORNAME`, которых
там нет; биржа отвечала `200`, строки приходили, значения оставались пустыми.
В хранилище накопилось 62 584 строки весов при пяти непустых и 506 строк
справочника отраслей, где отрасль не заполнена ни у одной.

**У биржи нет поля «сектор».** Сектор выводится из принадлежности бумаги к
отраслевым индексам MOEX — так делает оригинал, и другого источника отрасли
биржа не предоставляет.

Перенесено из `pipelines/iss_equity_sector_sync/` и
`pipelines/iss_index_constituents_daily_sync/` (`MR-MASTER-DRO`, `f07295e`):
оттуда взяты разделы, перечень отраслевых индексов и правило выбора при
вхождении бумаги в несколько из них.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from financial_ai.market_data.iss.client import IssClient, ResponseContractError
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import ASSET_PREFIX, asset_id_for, to_decimal
from financial_ai.market_data.sources.trading_calendar import parse_date
from financial_ai.market_data.verification import VerificationResult, one_session

logger = logging.getLogger(__name__)

SECTORS_SOURCE_ID = "equity_sectors"
CONSTITUENTS_SOURCE_ID = "index_constituents"

# Вес бумаги в индексе — глобальный ряд на актив: имя ряда несёт индекс и бумагу.
INDEX_WEIGHT_PREFIX = "IDX_WEIGHT_"

# Отраслевые индексы MOEX. Перечень перенесён из конфигурации оригинала
# (`iss_equity_sector_sync/config.py`), а не собран по догадке: он и есть
# определение того, какие отрасли биржа различает.
SECTOR_INDEX_IDS: tuple[str, ...] = (
    "MOEXOG",  # нефть и газ
    "MOEXMM",  # металлы и добыча
    "MOEXFN",  # финансы
    "MOEXTN",  # телекоммуникации
    "MOEXEU",  # электроэнергетика
    "MOEXTL",  # потребительский сектор
    "MOEXCH",  # химия и нефтехимия
    "MOEXIT",  # информационные технологии
    "MOEXCN",  # потребительские услуги
    "MOEXRE",  # недвижимость
    "MOEXINN",  # инновации
)


class ReferenceEmptyError(RuntimeError):
    """Раздел ответил, но значений не принёс.

    Отдельный тип, а не ноль строк: ровно так оба справочника и были сломаны —
    ответ приходил, строки писались, значений в них не было, и прогон считался
    успешным (FR-018).
    """


async def sync_sectors(
    client: IssClient, repository: MarketDataRepository, session_date: dt.date
) -> int:
    """Обновить отраслевую принадлежность эмитентов.

    Сектор бумаги — тот отраслевой индекс, в котором её вес наибольший. Берётся
    **текущий** состав индексов: у справочника нет оси сессий, и состав на дату
    ему не нужен.

    Одиннадцать обращений на весь справочник плюс одно за именами индексов —
    независимо от числа бумаг.
    """
    titles = await client.fetch_index_titles()

    # ticker -> (вес, дата, индекс). Ключ сравнения тот же, что в оригинале:
    # бумага может входить в несколько отраслевых индексов, и выбор не должен
    # зависеть от порядка ответа биржи.
    best: dict[str, tuple[Decimal, str, str]] = {}

    for index_id in SECTOR_INDEX_IDS:
        rows = await client.fetch_index_analytics(index_id)
        for ticker, weight, trade_date in _weights_from_analytics(rows):
            if weight is None:
                continue
            candidate = (weight, trade_date, index_id)
            current = best.get(ticker)
            if current is None or candidate > current:
                best[ticker] = candidate

    if not best:
        raise ReferenceEmptyError(
            "отраслевые индексы не принесли ни одной бумаги: раздел аналитики изменился"
        )

    # Ключ — СУЩНОСТЬ, а не имя. У переименованной бумаги отрасль уходила на
    # сущность, которой нет, а настоящая оставалась без неё: справочник пишет
    # строку по любому ключу и молчит (FR-048).
    aliases = await repository.aliases_on(session_date)
    sectors: dict[str, str | None] = {
        aliases.get(ticker, asset_id_for(ticker)): titles.get(index_id) or index_id
        for ticker, (_, _, index_id) in best.items()
    }

    written = await repository.upsert_sectors(sectors)
    logger.info(
        "секторы: бумаг с отраслью %d по %d отраслевым индексам, записано %d",
        len(sectors),
        len(SECTOR_INDEX_IDS),
        written,
    )
    return written


async def sync_index_constituents(
    client: IssClient,
    repository: MarketDataRepository,
    session_date: dt.date,
    index_id: str = "IMOEX",
) -> VerificationResult:
    """Собрать дневной состав индекса и веса бумаг."""
    rows = await client.fetch_index_analytics(index_id, session_date.isoformat())
    # Имя ряда несёт имя бумаги, а бумагу переименовывают: без канонического
    # имени переименование начинает второй ряд весов, а прежний обрывается
    # (FR-048).
    aliases = await repository.aliases_on(session_date)
    names = {ticker: asset_id.removeprefix(ASSET_PREFIX) for ticker, asset_id in aliases.items()}
    weights = rows_to_weights(rows, session_date, index_id, names)

    if rows and not weights:
        raise ReferenceEmptyError(
            f"состав индекса {index_id} за {session_date}: строк {len(rows)}, весов ни одного"
        )

    written = 0
    for series_id, values in weights.items():
        written += await repository.upsert_global_values(series_id, values)

    logger.info(
        "состав индекса %s за %s: бумаг %d, записано %d",
        index_id,
        session_date,
        len(weights),
        written,
    )
    return one_session(
        written,
        session_date,
        f"index:{index_id}",
        has_value=bool(weights),
    )


def rows_to_weights(
    rows: list[dict[str, object]],
    session_date: dt.date,
    index_id: str,
    names: dict[str, str] | None = None,
) -> dict[str, dict[dt.date, Decimal | None]]:
    """Веса бумаг в индексе как отдельные ряды.

    Отсутствие бумаги в составе — не нулевой вес, а отсутствие ряда на эту
    дату: иначе выбывшая из индекса бумага выглядела бы как бумага с нулевым
    весом, что для модели другое утверждение.

    Строки без веса пропускаются вовсе. Записанные, они выглядели бы собранным
    наблюдением и не дали бы догону собрать сессию заново — именно это и
    произошло с 62 584 строками прежнего источника.
    """
    canonical = names or {}
    out: dict[str, dict[dt.date, Decimal | None]] = {}
    for ticker, weight, trade_date in _weights_from_analytics(rows):
        if weight is None:
            continue
        # Дата наблюдения — запрошенная, а не пришедшая: раздел отдаёт состав на
        # ближайшую доступную дату, и записать его как наблюдение о запрошенной
        # сессии значило бы передатировать.
        day = parse_date(trade_date) or session_date
        if day != session_date:
            continue
        # Имя ряда составляется из КАНОНИЧЕСКОГО имени бумаги: иначе
        # переименование начинает второй ряд весов, а прежний обрывается
        # (FR-048).
        series_id = f"{INDEX_WEIGHT_PREFIX}{index_id}_{canonical.get(ticker, ticker)}"
        out.setdefault(series_id, {})[day] = weight
    return out


def _weights_from_analytics(
    rows: list[dict[str, object]],
) -> list[tuple[str, Decimal | None, str]]:
    """Тикер, вес и дата из строк раздела аналитики.

    Имена колонок раздела — в нижнем регистре, в отличие от истории торгов.
    """
    out: list[tuple[str, Decimal | None, str]] = []
    for row in rows:
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not ticker.strip():
            # Строка без тикера — нарушение контракта, а не строка, которую
            # можно пропустить: иначе состав индекса «подтверждался» неполным
            # (FR-032e).
            raise ResponseContractError(f"строка состава индекса без тикера: {ticker!r}")
        trade_date = row.get("tradedate")
        out.append(
            (
                ticker.strip().upper(),
                to_decimal(row.get("weight")),
                str(trade_date) if trade_date else "",
            )
        )
    return out
