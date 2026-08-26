"""Доменные расчёты портфеля (T022).

Проверяются формулы и, отдельно, граничные случаи из спецификации: они
не должны превращаться в ложные нули.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from financial_ai.domain.models import BrokerSnapshot
from financial_ai.domain.portfolio import (
    age_seconds,
    build_snapshot,
    is_stale,
    percent,
    position_pnl,
    position_value,
    share,
    stale_after_seconds,
)
from tests.fakes.fake_broker import DEFAULT_ACCOUNT, make_position, make_snapshot


def test_position_value_is_quantity_times_price() -> None:
    assert position_value(Decimal("1200"), Decimal("301.72")) == Decimal("362064.00")


def test_pnl_is_value_minus_cost_basis() -> None:
    assert position_pnl(Decimal("362064"), Decimal("337680")) == Decimal("24384")


def test_pnl_is_unknown_without_cost_basis() -> None:
    assert position_pnl(Decimal("362064"), None) is None


def test_share_of_zero_total_is_zero_without_division() -> None:
    assert share(Decimal("100"), Decimal("0")) == Decimal("0")


def test_percent_of_zero_base_is_none_not_false_zero() -> None:
    # Ноль процентов и «процент не определён» — разные вещи.
    assert percent(Decimal("100"), Decimal("0")) is None
    assert percent(Decimal("0"), Decimal("100")) == Decimal("0")


def test_percent_is_none_when_numerator_unknown() -> None:
    assert percent(None, Decimal("100")) is None


def test_snapshot_totals_reconcile() -> None:
    snapshot = build_snapshot(make_snapshot(cash="40545"))

    positions_value = sum(p.value for p in snapshot.positions)

    # SC-003: сумма стоимостей позиций и денежных средств равна общей стоимости.
    assert snapshot.total_value == positions_value + snapshot.cash
    assert snapshot.positions_count == 1


def test_snapshot_shares_sum_to_one() -> None:
    snapshot = build_snapshot(
        make_snapshot(
            cash="10000",
            positions=(
                make_position(instrument_uid="a", quantity="10", current_price="100"),
                make_position(instrument_uid="b", quantity="5", current_price="200"),
            ),
        )
    )

    total = snapshot.total_value
    shares = [share(p.value, total) for p in snapshot.positions]
    shares.append(share(snapshot.cash, total))

    assert sum(shares) == Decimal("1")


def test_empty_portfolio_has_zero_totals_and_no_false_percent() -> None:
    snapshot = build_snapshot(make_snapshot(cash="0", positions=()))

    assert snapshot.total_value == Decimal("0")
    assert snapshot.positions_count == 0
    # Процент не рассчитывается: базы нет.
    assert percent(snapshot.unrealized_pnl, snapshot.positions_cost_basis) is None


def test_position_without_average_price_does_not_pollute_cost_basis() -> None:
    snapshot = build_snapshot(
        make_snapshot(
            cash="0",
            positions=(
                make_position(
                    instrument_uid="a", quantity="10", average_price="100", current_price="120"
                ),
                # Внешнее зачисление: цена приобретения неизвестна.
                make_position(
                    instrument_uid="b", quantity="10", average_price=None, current_price="50"
                ),
            ),
        )
    )

    unknown = next(p for p in snapshot.positions if p.instrument_uid == "b")
    assert unknown.unrealized_pnl is None
    assert unknown.cost_basis is None
    # В базу вошла только позиция с известной ценой приобретения.
    assert snapshot.positions_cost_basis == Decimal("1000")
    assert snapshot.unrealized_pnl == Decimal("200")


def test_short_position_is_handled_without_distorting_totals() -> None:
    snapshot = build_snapshot(
        make_snapshot(
            cash="100000",
            positions=(
                make_position(
                    instrument_uid="long", quantity="10", average_price="100", current_price="150"
                ),
                make_position(
                    instrument_uid="short", quantity="-4", average_price="200", current_price="180"
                ),
            ),
        )
    )

    short = next(p for p in snapshot.positions if p.instrument_uid == "short")

    assert short.value == Decimal("-720")
    # Короткая позиция в плюсе: цена упала.
    assert short.unrealized_pnl == Decimal("80")
    assert snapshot.total_value == Decimal("100000") + Decimal("1500") + Decimal("-720")


def test_position_without_ticker_and_name_is_preserved() -> None:
    snapshot = build_snapshot(
        make_snapshot(positions=(make_position(ticker=None, name=None, instrument_uid="uid-only"),))
    )

    position = snapshot.positions[0]
    assert position.ticker is None
    assert position.name is None
    assert position.instrument_uid == "uid-only"


def test_stale_threshold_never_below_three_minutes() -> None:
    # FR-040: max(3 × интервал, 180 c).
    assert stale_after_seconds(15) == 180
    assert stale_after_seconds(60) == 180
    assert stale_after_seconds(120) == 360
    assert stale_after_seconds(3600) == 10800


def test_is_stale_uses_threshold() -> None:
    now = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)

    fresh = now - dt.timedelta(seconds=170)
    old = now - dt.timedelta(seconds=181)

    assert is_stale(fresh, now, 60) is False
    assert is_stale(old, now, 60) is True


def test_missing_snapshot_is_not_stale() -> None:
    now = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)

    # Отсутствие данных — отдельное состояние, а не «данные несвежие».
    assert is_stale(None, now, 60) is False
    assert age_seconds(None, now) is None


def test_age_is_never_negative() -> None:
    now = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)
    future = now + dt.timedelta(seconds=5)

    assert age_seconds(future, now) == 0


def test_build_snapshot_preserves_captured_at() -> None:
    captured = dt.datetime(2026, 8, 26, 11, 32, 18, tzinfo=dt.UTC)
    raw = BrokerSnapshot(
        account=DEFAULT_ACCOUNT,
        captured_at=captured,
        cash=Decimal("0"),
        positions=(),
    )

    assert build_snapshot(raw).captured_at == captured
