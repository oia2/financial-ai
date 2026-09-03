"""Тесты отличия пропуска от нуля.

Отсутствие наблюдения не заменяется нулём и не достраивается соседними
сессиями. Ошибка здесь не падает тестом сама: она превращается в правдоподобные
данные, на которых модель обучится неверному.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from financial_ai.market_data.sources.equity_d1 import rows_to_bars

SESSION = dt.date(2026, 8, 28)


def test_absent_price_stays_none() -> None:
    rows = [
        {"SECID": "SBER", "OPEN": None, "HIGH": None, "LOW": None, "CLOSE": None, "VOLUME": None}
    ]
    bar = rows_to_bars(rows, SESSION)[0]
    assert bar.open is None
    assert bar.close is None
    assert bar.volume is None


def test_absent_price_is_not_zero() -> None:
    """Ноль означал бы «торговалось по нулю», а не «не торговалось»."""
    bar = rows_to_bars([{"SECID": "SBER", "CLOSE": None}], SESSION)[0]
    assert bar.close is None
    assert bar.close != Decimal("0")


def test_partial_row_keeps_present_values() -> None:
    """Отсутствие части значений не отменяет остальные."""
    rows = [{"SECID": "SBER", "OPEN": "312.4", "HIGH": None, "LOW": None, "CLOSE": "314.22"}]
    bar = rows_to_bars(rows, SESSION)[0]
    assert bar.open == Decimal("312.4")
    assert bar.high is None
    assert bar.close == Decimal("314.22")


def test_row_without_ticker_is_dropped() -> None:
    """Строка без тикера ни к чему не относится — сохранять её некуда."""
    rows = [
        {"SECID": None, "CLOSE": "1"},
        {"SECID": "  ", "CLOSE": "2"},
        {"SECID": "SBER", "CLOSE": "3"},
    ]
    bars = rows_to_bars(rows, SESSION)
    assert [b.asset_id for b in bars] == ["EQ_AST_SBER"]


def test_duplicate_ticker_does_not_create_two_rows() -> None:
    """Ключ price_series_id + session_date обязан остаться ключом."""
    rows = [{"SECID": "SBER", "CLOSE": "1"}, {"SECID": "SBER", "CLOSE": "2"}]
    bars = rows_to_bars(rows, SESSION)
    assert len(bars) == 1
    assert bars[0].close == Decimal("1")


def test_revision_changes_when_value_changes() -> None:
    """Отпечаток нужен, чтобы переиздание биржей не прошло молча."""
    first = rows_to_bars([{"SECID": "SBER", "CLOSE": "314.22"}], SESSION)[0]
    second = rows_to_bars([{"SECID": "SBER", "CLOSE": "314.23"}], SESSION)[0]
    assert first.revision() != second.revision()


def test_revision_is_stable_for_same_values() -> None:
    rows = [{"SECID": "SBER", "OPEN": "312.4", "CLOSE": "314.22"}]
    assert rows_to_bars(rows, SESSION)[0].revision() == rows_to_bars(rows, SESSION)[0].revision()


def test_revision_distinguishes_none_from_zero() -> None:
    """Иначе пропуск и ноль дали бы один отпечаток, и подмена не заметилась бы."""
    absent = rows_to_bars([{"SECID": "SBER", "CLOSE": None}], SESSION)[0]
    zero = rows_to_bars([{"SECID": "SBER", "CLOSE": "0"}], SESSION)[0]
    assert absent.revision() != zero.revision()
