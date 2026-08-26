"""Маппинг денежных типов T-Invest в Decimal (T023).

Это место, где ломается точность: ``units`` + ``nano`` обязаны переводиться
в ``Decimal`` без промежуточного ``float`` (SC-002). Ожидаемые значения
задаются как ``Decimal``; сравнение с ``float`` в этом файле недопустимо.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from t_tech.invest import MoneyValue, Quotation

from financial_ai.broker.money import currency_of, to_decimal, to_decimal_or_zero
from financial_ai.broker.tinvest import _mask


@pytest.mark.parametrize(
    ("units", "nano", "expected"),
    [
        (0, 0, "0"),
        (1, 0, "1"),
        (0, 500_000_000, "0.5"),
        (301, 720_000_000, "301.72"),
        # Отрицательные величины: знак несут обе части.
        (-1, -500_000_000, "-1.5"),
        (-720, 0, "-720"),
        # Предельная nano-точность — одна миллиардная.
        (0, 1, "0.000000001"),
        (0, 999_999_999, "0.999999999"),
        (-0, -1, "-0.000000001"),
        # Крупные суммы: разрядность не теряется.
        (999_999_999_999, 999_999_999, "999999999999.999999999"),
    ],
)
def test_quotation_maps_to_exact_decimal(units: int, nano: int, expected: str) -> None:
    assert to_decimal(Quotation(units=units, nano=nano)) == Decimal(expected)


def test_money_value_maps_to_exact_decimal() -> None:
    value = MoneyValue(currency="rub", units=40_545, nano=250_000_000)

    assert to_decimal(value) == Decimal("40545.25")


def test_mapping_is_exact_where_float_would_drift() -> None:
    # 0.1 + 0.2 в double даёт 0.30000000000000004; Decimal обязан дать ровно 0.3.
    tenth = to_decimal(Quotation(units=0, nano=100_000_000))
    fifth = to_decimal(Quotation(units=0, nano=200_000_000))

    assert tenth is not None and fifth is not None
    assert tenth + fifth == Decimal("0.3")


def test_sum_of_many_positions_stays_exact() -> None:
    # Длинный портфель — как раз тот случай, где double накапливает ошибку.
    price = to_decimal(Quotation(units=0, nano=10_000_000))
    assert price is not None

    total = sum((price for _ in range(1000)), Decimal(0))

    assert total == Decimal("10")


def test_missing_value_maps_to_none_and_zero_variant() -> None:
    assert to_decimal(None) is None
    assert to_decimal_or_zero(None) == Decimal("0")


def test_currency_is_normalised_to_upper_case() -> None:
    assert currency_of(MoneyValue(currency="rub", units=1, nano=0)) == "RUB"
    # Quotation валюты не несёт — используется значение по умолчанию.
    assert currency_of(Quotation(units=1, nano=0)) == "RUB"


def test_account_id_is_masked_to_last_four_digits() -> None:
    # Полный номер договора наружу не уходит (FR-022, SC-009).
    assert _mask("2000124821") == "•• 4821"
    assert _mask("") == "••"
