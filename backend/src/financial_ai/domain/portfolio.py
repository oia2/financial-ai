"""Расчёты портфеля: стоимость, P&L, доли, возраст и свежесть данных.

Граничные случаи заданы спецификацией и не должны превращаться в ложные нули:

* ``total_value == 0`` — доли равны нулю, деления не происходит;
* нулевая база стоимости — процент P&L не определён и отдаётся как ``None``;
* отрицательное количество (короткая позиция) считается корректным.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from financial_ai.domain.models import (
    AccountSnapshot,
    BrokerPosition,
    BrokerSnapshot,
    PositionState,
)

ZERO = Decimal(0)

# Нижняя граница порога устаревания: при малых интервалах троекратный интервал
# давал бы ложные срабатывания (FR-040).
MIN_STALE_AFTER_SECONDS = 180
STALE_INTERVAL_FACTOR = 3


def position_value(quantity: Decimal, current_price: Decimal) -> Decimal:
    """Стоимость позиции."""
    return quantity * current_price


def position_cost_basis(quantity: Decimal, average_price: Decimal | None) -> Decimal | None:
    """Стоимость приобретения. ``None``, если средняя цена неизвестна."""
    if average_price is None:
        return None
    return quantity * average_price


def position_pnl(value: Decimal, cost_basis: Decimal | None) -> Decimal | None:
    """Нереализованный P&L. ``None``, если база неизвестна."""
    if cost_basis is None:
        return None
    return value - cost_basis


def share(value: Decimal, total: Decimal) -> Decimal:
    """Доля в портфеле. При нулевом итоге — ноль, без деления."""
    if total == ZERO:
        return ZERO
    return value / total


def percent(numerator: Decimal | None, base: Decimal | None) -> Decimal | None:
    """Относительная величина как доля единицы.

    ``None`` при неизвестной или нулевой базе: ноль процентов и «процент не
    определён» — разные вещи.
    """
    if numerator is None or base is None or base == ZERO:
        return None
    return numerator / base


def build_position_state(position: BrokerPosition) -> PositionState:
    """Достраивает позицию расчётными величинами."""
    value = position_value(position.quantity, position.current_price)
    cost_basis = position_cost_basis(position.quantity, position.average_price)

    return PositionState(
        instrument_uid=position.instrument_uid,
        ticker=position.ticker,
        name=position.name,
        asset_type=position.asset_type,
        currency=position.currency,
        quantity=position.quantity,
        average_price=position.average_price,
        current_price=position.current_price,
        value=value,
        unrealized_pnl=position_pnl(value, cost_basis),
        cost_basis=cost_basis,
    )


def build_snapshot(broker_snapshot: BrokerSnapshot) -> AccountSnapshot:
    """Собирает состояние счёта из данных брокера.

    Общая стоимость считается как сумма стоимостей позиций и денежных средств.
    Именно поэтому отображаемые суммы всегда сходятся, а доли дают 100%
    (SC-003): значение не берётся из отдельного поля брокера, которое могло бы
    с этой суммой разойтись.
    """
    positions = tuple(build_position_state(p) for p in broker_snapshot.positions)

    positions_value = sum((p.value for p in positions), ZERO)
    total_value = positions_value + broker_snapshot.cash

    # В базу процентного P&L входят только позиции с известной ценой
    # приобретения — иначе процент был бы посчитан от неполной базы.
    cost_basis = sum((p.cost_basis for p in positions if p.cost_basis is not None), ZERO)
    pnl = sum((p.unrealized_pnl for p in positions if p.unrealized_pnl is not None), ZERO)

    return AccountSnapshot(
        captured_at=broker_snapshot.captured_at,
        total_value=total_value,
        cash=broker_snapshot.cash,
        positions_cost_basis=cost_basis,
        unrealized_pnl=pnl,
        positions_count=len(positions),
        positions=positions,
    )


def stale_after_seconds(interval_seconds: int) -> int:
    """Порог устаревания данных: ``max(3 × интервал, 180 с)`` (FR-040)."""
    return max(STALE_INTERVAL_FACTOR * interval_seconds, MIN_STALE_AFTER_SECONDS)


def age_seconds(captured_at: dt.datetime | None, now: dt.datetime) -> int | None:
    """Возраст данных в секундах. ``None``, если снимка ещё нет."""
    if captured_at is None:
        return None
    return max(0, int((now - captured_at).total_seconds()))


def is_stale(captured_at: dt.datetime | None, now: dt.datetime, interval_seconds: int) -> bool:
    """Устарели ли данные.

    Отсутствие снимка устареванием не считается: это отдельное состояние
    «данных ещё нет», а не «данные несвежие» (US4 AS4).
    """
    age = age_seconds(captured_at, now)
    if age is None:
        return False
    return age > stale_after_seconds(interval_seconds)
