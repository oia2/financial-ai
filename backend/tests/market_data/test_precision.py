"""Тесты точности цен.

Значение, полученное от биржи, должно дойти до хранилища без искажений.
`float` на этом пути запрещён: пройдя через него, значение уже не восстановить.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from financial_ai.market_data.sources.equity_d1 import rows_to_bars, to_decimal

SESSION = dt.date(2026, 8, 28)


@pytest.mark.parametrize(
    "raw",
    ["312.4", "0.000000001", "123456789.123456789", "1e-9", "-15.75", "0"],
)
def test_decimal_roundtrip_is_exact(raw: str) -> None:
    parsed = to_decimal(raw)
    assert parsed == Decimal(raw)


def test_nine_decimal_places_survive() -> None:
    """NUMERIC(28,9): девять знаков после запятой должны сохраниться."""
    value = to_decimal("0.123456789")
    assert value is not None
    assert str(value) == "0.123456789"


def test_float_input_does_not_lose_precision_silently() -> None:
    """Даже если биржа пришлёт число, разбор идёт через строку.

    `Decimal(0.1)` дало бы 0.1000000000000000055511151231257827, а
    `Decimal(str(0.1))` — ровно 0.1.
    """
    assert to_decimal(0.1) == Decimal("0.1")


def test_missing_value_is_none_not_zero() -> None:
    """Отсутствие наблюдения и нулевая цена — разные факты."""
    assert to_decimal(None) is None
    assert to_decimal("") is None
    assert to_decimal("0") == Decimal("0")
    assert to_decimal("0") is not None


def test_unparsable_value_becomes_none() -> None:
    assert to_decimal("н/д") is None


def test_bars_carry_exact_prices() -> None:
    rows = [
        {
            "SECID": "SBER",
            "OPEN": "312.400000001",
            "HIGH": "315.1",
            "LOW": "311.05",
            "CLOSE": "314.22",
            "VOLUME": "12345678",
        }
    ]
    bars = rows_to_bars(rows, SESSION)
    assert bars[0].open == Decimal("312.400000001")
    assert bars[0].close == Decimal("314.22")


def test_no_float_on_the_parsing_path() -> None:
    """Сторожевой тест: тип значения — Decimal, а не float."""
    bars = rows_to_bars([{"SECID": "SBER", "CLOSE": "314.22"}], SESSION)
    assert isinstance(bars[0].close, Decimal)
    assert not isinstance(bars[0].close, float)
