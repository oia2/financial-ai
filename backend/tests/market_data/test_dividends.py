"""Тесты дивидендов.

Главное здесь — сопоставление тикера с идентификатором брокера: именно его
отсутствие было настоящим блокером, а не токен.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from financial_ai.market_data.sources.dividends import (
    match_instruments,
    response_to_rows,
)


@dataclass
class Share:
    ticker: str
    figi: str
    currency: str = "rub"


@dataclass
class Quotation:
    units: int
    nano: int


@dataclass
class Dividend:
    record_date: dt.datetime | None
    declared_date: dt.datetime | None = None
    last_buy_date: dt.datetime | None = None
    payment_date: dt.datetime | None = None
    dividend_net: Quotation | None = None


KNOWN = {"SBER", "GAZP", "LKOH"}


# --- сопоставление тикеров ---------------------------------------------------


def test_known_tickers_are_matched() -> None:
    instruments = [Share("SBER", "BBG004730N88"), Share("GAZP", "BBG004730RP0")]
    assert match_instruments(instruments, KNOWN) == {
        "SBER": "BBG004730N88",
        "GAZP": "BBG004730RP0",
    }


def test_unknown_tickers_are_skipped() -> None:
    """Сопоставляем только те бумаги, данные которых у нас есть."""
    instruments = [Share("SBER", "F1"), Share("ЧУЖОЙ", "F2")]
    assert match_instruments(instruments, KNOWN) == {"SBER": "F1"}


def test_non_ruble_instruments_are_skipped() -> None:
    """Тикер вне рублёвого рынка может совпасть с чужим.

    Такое соответствие хуже отсутствующего: дивиденды приписались бы не той
    бумаге, и заметить это было бы нечем.
    """
    instruments = [Share("SBER", "USD_FIGI", currency="usd"), Share("GAZP", "RUB_FIGI")]
    assert match_instruments(instruments, KNOWN) == {"GAZP": "RUB_FIGI"}


def test_instrument_without_figi_is_skipped() -> None:
    assert match_instruments([Share("SBER", "")], KNOWN) == {}


def test_ticker_case_is_normalised() -> None:
    assert match_instruments([Share("sber", "F1")], KNOWN) == {"SBER": "F1"}


# --- разбор событий ----------------------------------------------------------


def test_dividend_is_parsed() -> None:
    events = response_to_rows(
        "SBER",
        [
            Dividend(
                record_date=dt.datetime(2026, 5, 12, tzinfo=dt.UTC),
                payment_date=dt.datetime(2026, 5, 26, tzinfo=dt.UTC),
                dividend_net=Quotation(units=34, nano=840000000),
            )
        ],
    )
    assert len(events) == 1
    assert events[0].asset_id == "EQ_AST_SBER"
    assert events[0].record_date == dt.date(2026, 5, 12)
    assert events[0].value == Decimal("34.84")


def test_money_keeps_nano_precision() -> None:
    """Брокер отдаёт деньги парой «целые + нано»: float потерял бы точность."""
    events = response_to_rows(
        "SBER",
        [
            Dividend(
                record_date=dt.datetime(2026, 5, 12, tzinfo=dt.UTC), dividend_net=Quotation(0, 1)
            )
        ],
    )
    assert events[0].value == Decimal("0.000000001")


def test_event_without_record_date_is_skipped() -> None:
    """Дата фиксации реестра — часть ключа: без неё событие некуда положить."""
    assert response_to_rows("SBER", [Dividend(record_date=None)]) == []


def test_event_without_amount_stays_none_not_zero() -> None:
    """«Дивиденд не объявлен» и «дивиденд нулевой» — разные факты."""
    events = response_to_rows(
        "SBER", [Dividend(record_date=dt.datetime(2026, 5, 12, tzinfo=dt.UTC))]
    )
    assert events[0].value is None


def test_optional_dates_are_carried() -> None:
    events = response_to_rows(
        "SBER",
        [
            Dividend(
                record_date=dt.datetime(2026, 5, 12, tzinfo=dt.UTC),
                declared_date=dt.datetime(2026, 4, 1, tzinfo=dt.UTC),
                last_buy_date=dt.datetime(2026, 5, 10, tzinfo=dt.UTC),
            )
        ],
    )
    assert events[0].declared_date == dt.date(2026, 4, 1)
    assert events[0].last_buy_date == dt.date(2026, 5, 10)
