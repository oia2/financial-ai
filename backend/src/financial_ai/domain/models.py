"""Внутренние модели Financial AI.

DTO брокера не покидают пакет ``financial_ai.broker``: адаптер приводит их к
этим моделям, и дальше система работает только с ними. Замена брокера
потребует нового адаптера и ничего больше.

Все денежные, ценовые и количественные величины — ``Decimal``.
``float`` не используется нигде на пути «брокер → БД → API → JSON» (SC-002).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    """Брокерский счёт в том виде, в каком его отдал брокер."""

    broker_account_id: str
    # Только маскированный вид: полный номер договора не хранится (FR-022).
    masked_id: str
    display_name: str
    currency: str


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """Позиция, полученная от брокера, до расчёта производных величин."""

    instrument_uid: str
    ticker: str | None
    name: str | None
    asset_type: str | None
    currency: str
    # Может быть отрицательным — короткая позиция.
    quantity: Decimal
    # None, если брокер не дал цену приобретения (например, внешнее зачисление).
    average_price: Decimal | None
    current_price: Decimal


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    """Сырое состояние счёта на момент времени."""

    account: BrokerAccount
    captured_at: dt.datetime
    cash: Decimal
    positions: tuple[BrokerPosition, ...]
    # Общая стоимость по данным брокера. Используется как перекрёстная
    # проверка расчёта, а не как источник отображаемого значения.
    broker_total_value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PositionState:
    """Позиция с рассчитанными стоимостью и P&L."""

    instrument_uid: str
    ticker: str | None
    name: str | None
    asset_type: str | None
    currency: str
    quantity: Decimal
    average_price: Decimal | None
    current_price: Decimal
    value: Decimal
    # None, если неизвестна средняя цена: ложный ноль недопустим.
    unrealized_pnl: Decimal | None
    cost_basis: Decimal | None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Рассчитанное состояние счёта — то, что сохраняется и отображается."""

    captured_at: dt.datetime
    total_value: Decimal
    cash: Decimal
    positions_cost_basis: Decimal
    unrealized_pnl: Decimal
    positions_count: int
    positions: tuple[PositionState, ...]
