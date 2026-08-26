"""Перевод денежных типов T-Invest API в ``Decimal``.

T-Invest отдаёт ``Quotation`` и ``MoneyValue`` парой целых: ``units`` —
целая часть, ``nano`` — доля в миллиардных. Точное значение —
``units + nano / 1_000_000_000``.

Промежуточный ``float`` здесь запрещён: он теряет точность уже на обычных
котировках, а SC-002 требует совпадения с брокером до копейки.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

NANO_IN_UNIT = Decimal(1_000_000_000)


@runtime_checkable
class HasUnitsNano(Protocol):
    """Структурный тип ``Quotation`` и ``MoneyValue``."""

    units: int
    nano: int


def to_decimal(value: HasUnitsNano | None) -> Decimal | None:
    """Точное значение или ``None``, если поле не задано.

    У отрицательных величин T-Invest делает отрицательными обе части,
    поэтому сложение даёт корректный знак без дополнительной обработки.
    """
    if value is None:
        return None
    return Decimal(value.units) + Decimal(value.nano) / NANO_IN_UNIT


def to_decimal_or_zero(value: HasUnitsNano | None) -> Decimal:
    """То же, но отсутствующее значение трактуется как ноль."""
    result = to_decimal(value)
    return Decimal(0) if result is None else result


def currency_of(value: object, default: str = "RUB") -> str:
    """Валюта ``MoneyValue``. ``Quotation`` валюты не несёт."""
    currency = getattr(value, "currency", None)
    if isinstance(currency, str) and currency:
        return currency.upper()
    return default
