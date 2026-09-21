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
from financial_ai.market_data.verification import VerificationResult, one_session

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
) -> VerificationResult:
    """Собрать котировки всех бумаг за одну торговую сессию.

    Одно обращение к бирже, а не одно на бумагу.

    Переименованная бумага остаётся прежней сущностью: наблюдение под новым
    именем ложится в ряд, опознанный псевдонимом, а не заводит второй ряд с
    оборванной историей (FR-038).
    """
    rows = await client.fetch_session_rows(session_date.isoformat(), COLUMNS)
    aliases = await repository.aliases_on(session_date)
    bars = rows_to_bars(rows, session_date, aliases)

    for bar in bars:
        ticker = bar.asset_id.removeprefix(ASSET_PREFIX)
        await repository.upsert_asset(bar.asset_id, ticker, session_date)
        await repository.upsert_price_series(bar.price_series_id, bar.asset_id, session_date)

    written = await repository.upsert_daily_bars(bars)
    logger.info("котировки за %s: получено строк %d, записано %d", session_date, len(rows), written)
    return one_session(written, session_date, "board:TQBR", has_value=bool(rows))


def rows_to_bars(
    rows: list[dict[str, object]],
    session_date: dt.date,
    aliases: dict[str, str] | None = None,
) -> list[DailyBar]:
    """Преобразовать ответ биржи в наблюдения.

    Строка без тикера пропускается: она ни к чему не относится. Строка без
    цен сохраняется с ``None`` — отсутствие наблюдения это факт, а не ноль.

    ``aliases`` отображает действующее имя бумаги на её сущность. Без него
    переименование выглядело бы появлением новой бумаги, а прежний ряд — как
    ушедшая с торгов.
    """
    known = aliases or {}
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

        asset_id = known.get(ticker, asset_id_for(ticker))
        series_id = asset_id.replace(ASSET_PREFIX, SERIES_PREFIX, 1)

        bars.append(
            DailyBar(
                asset_id=asset_id,
                price_series_id=series_id,
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

    ``Decimal`` на входе возвращается как есть: ответы биржи разбираются сразу
    в него (`iss/client.py:_loads`), и лишний проход через ``str`` здесь ничего
    бы не изменил.

    ``float`` тоже принимается — его приносят источники, чей ответ не JSON, —
    но к этому моменту значение уже искажено, и перевод через ``str`` потерю не
    возвращает. Точка, где потеря предотвращается, — разбор ответа, а не эта
    функция.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, float):
        logger.warning(
            "числовое значение пришло как float (%r): точность уже потеряна до разбора", raw
        )
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        logger.warning("не удалось разобрать числовое значение %r", raw)
        return None
