"""Дивиденды: единственный источник не с биржи, а от брокера.

Блокером здесь был не токен — он у worker уже есть, — а **идентификаторы**.
Брокер отвечает на `figi`/`instrument_id`, а собранные рыночные данные ключуются
по `asset_id` вида `EQ_AST_SBER`. Переводчика между ними не существовало, и в
спецификациях фич 002 и 003 он вынесен за объём.

Здесь он появляется в минимальном виде: справочник акций брокера сопоставляется
с нашими активами **по тикеру MOEX**. Это осознанно узкое решение, и его границы
надо знать:

- сопоставление идёт по тикеру, а тикер — не идентификатор сущности. При
  переименовании бумаги соответствие придётся восстанавливать;
- берутся только бумаги российского рынка в рублях: у остальных тикер может
  совпасть с чужим.

Полноценное сопоставление `asof_date + asset_id` ↔ FIGI с учётом истории
переименований остаётся за рамками — оно понадобится портфельному слою, и делать
его наспех здесь значило бы закопать проблему.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from financial_ai.market_data.repository import DividendRow, MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import asset_id_for

logger = logging.getLogger(__name__)

SOURCE_ID = "dividends"

# Валюта и рынок, на которых тикер MOEX однозначен.
RUB = "rub"


async def sync_dividends(
    client: Any,
    repository: MarketDataRepository,
    session_date: dt.date,
    known_tickers: set[str],
    history_days: int = 365,
) -> int:
    """Собрать дивидендные события по известным нам активам.

    Недоступность справочника или отдельной бумаги не отменяет остальные:
    дивиденды — дополняющий источник, а не основание для отказа всего сбора.
    """
    figi_by_ticker = await build_ticker_to_figi(client, known_tickers)
    if not figi_by_ticker:
        logger.warning("дивиденды: не удалось сопоставить ни одной бумаги с брокером")
        return 0

    date_from = session_date - dt.timedelta(days=history_days)
    events: list[DividendRow] = []

    for ticker, figi in sorted(figi_by_ticker.items()):
        try:
            response = await client.instruments.get_dividends(
                figi=figi, from_=_as_datetime(date_from), to=_as_datetime(session_date)
            )
        except Exception as error:  # noqa: BLE001 — одна бумага не отменяет остальные
            logger.warning("дивиденды: %s не получены (%s)", ticker, error)
            continue
        events.extend(response_to_rows(ticker, getattr(response, "dividends", []) or []))

    written = await repository.upsert_dividends(events)
    logger.info(
        "дивиденды: сопоставлено бумаг %d, событий записано %d", len(figi_by_ticker), written
    )
    return written


async def build_ticker_to_figi(client: Any, known_tickers: set[str]) -> dict[str, str]:
    """Сопоставить наши тикеры с идентификаторами брокера.

    Справочник запрашивается целиком одним обращением: перебирать по бумаге
    значило бы сотни запросов ради того же результата.
    """
    try:
        response = await client.instruments.shares()
    except Exception as error:  # noqa: BLE001 — без справочника дивидендов просто нет
        logger.warning("дивиденды: справочник акций недоступен (%s)", error)
        return {}

    return match_instruments(getattr(response, "instruments", []) or [], known_tickers)


def match_instruments(instruments: list[Any], known_tickers: set[str]) -> dict[str, str]:
    """Отобрать рублёвые бумаги, тикеры которых нам известны."""
    out: dict[str, str] = {}
    for instrument in instruments:
        ticker = (getattr(instrument, "ticker", "") or "").strip().upper()
        figi = (getattr(instrument, "figi", "") or "").strip()
        currency = (getattr(instrument, "currency", "") or "").strip().lower()
        if not ticker or not figi or ticker not in known_tickers:
            continue
        if currency and currency != RUB:
            # Тикер вне рублёвого рынка может совпасть с чужим — такое
            # соответствие хуже отсутствующего.
            continue
        out[ticker] = figi
    return out


def response_to_rows(ticker: str, dividends: list[Any]) -> list[DividendRow]:
    """Преобразовать ответ брокера в события выплат.

    Пропуск остаётся пропуском: событие без суммы сохраняется с ``None``, а не
    с нулём — «дивиденд не объявлен» и «дивиденд нулевой» разные факты.
    """
    rows: list[DividendRow] = []
    for item in dividends:
        record_date = _as_date(getattr(item, "record_date", None))
        if record_date is None:
            continue
        rows.append(
            DividendRow(
                asset_id=asset_id_for(ticker),
                record_date=record_date,
                declared_date=_as_date(getattr(item, "declared_date", None)),
                last_buy_date=_as_date(getattr(item, "last_buy_date", None)),
                payment_date=_as_date(getattr(item, "payment_date", None)),
                value=_quotation_to_decimal(getattr(item, "dividend_net", None)),
            )
        )
    return rows


def _as_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def _as_datetime(value: dt.date) -> dt.datetime:
    return dt.datetime.combine(value, dt.time.min, tzinfo=dt.UTC)


def _quotation_to_decimal(value: Any) -> Decimal | None:
    """Сумма брокера в `Decimal`.

    T-Invest отдаёт деньги парой «целые + нано». Складывать их через `float`
    нельзя — точность потеряется на первом же значении.
    """
    if value is None:
        return None
    units = getattr(value, "units", None)
    nano = getattr(value, "nano", None)
    if units is None and nano is None:
        return None
    return Decimal(int(units or 0)) + Decimal(int(nano or 0)) / Decimal(1_000_000_000)
