"""Валидация ответа брокера (T024, FR-004).

Не прошедшее проверку состояние не должно попасть в БД: пользователь увидит
прежние данные с предупреждением, а не подменённые неверными.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from financial_ai.broker.errors import BrokerValidationError, FailureReason
from financial_ai.broker.validation import validate_broker_snapshot
from tests.fakes.fake_broker import make_position, make_snapshot


def test_valid_snapshot_passes() -> None:
    validate_broker_snapshot(make_snapshot())


def test_empty_portfolio_is_valid() -> None:
    validate_broker_snapshot(make_snapshot(cash="0", positions=()))


def test_account_without_id_is_rejected() -> None:
    snapshot = make_snapshot()
    broken = replace(snapshot, account=replace(snapshot.account, broker_account_id=""))

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(broken)


def test_account_without_currency_is_rejected() -> None:
    snapshot = make_snapshot()
    broken = replace(snapshot, account=replace(snapshot.account, currency=""))

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(broken)


def test_position_without_instrument_id_is_rejected() -> None:
    snapshot = make_snapshot(positions=(make_position(instrument_uid=""),))

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(snapshot)


def test_duplicate_instrument_is_rejected() -> None:
    snapshot = make_snapshot(
        positions=(make_position(instrument_uid="dup"), make_position(instrument_uid="dup"))
    )

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(snapshot)


def test_negative_current_price_is_rejected() -> None:
    snapshot = make_snapshot(positions=(make_position(current_price="-1"),))

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(snapshot)


def test_negative_average_price_is_rejected() -> None:
    snapshot = make_snapshot(positions=(make_position(average_price="-10"),))

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(snapshot)


def test_position_missing_currency_is_rejected() -> None:
    snapshot = make_snapshot(positions=(make_position(currency=""),))

    with pytest.raises(BrokerValidationError):
        validate_broker_snapshot(snapshot)


def test_totals_matching_broker_pass_reconciliation() -> None:
    # 1200 × 301.72 + 40545 = 402609
    snapshot = make_snapshot(cash="40545", broker_total_value="402609")

    validate_broker_snapshot(snapshot)


def test_rounding_difference_is_tolerated() -> None:
    snapshot = make_snapshot(cash="40545", broker_total_value="402609.4")

    validate_broker_snapshot(snapshot)


def test_material_mismatch_with_broker_total_is_rejected() -> None:
    snapshot = make_snapshot(cash="40545", broker_total_value="500000")

    with pytest.raises(BrokerValidationError) as error:
        validate_broker_snapshot(snapshot)

    assert error.value.reason is FailureReason.VALIDATION_FAILED


def test_missing_broker_total_skips_reconciliation() -> None:
    # Брокер не прислал итог — сверять не с чем, но состояние валидно.
    validate_broker_snapshot(make_snapshot(broker_total_value=None))


def test_short_position_does_not_break_reconciliation() -> None:
    snapshot = make_snapshot(
        cash="100000",
        positions=(make_position(instrument_uid="short", quantity="-4", current_price="180"),),
        broker_total_value="99280",
    )

    validate_broker_snapshot(snapshot)


def test_zero_quantity_position_is_allowed() -> None:
    snapshot = make_snapshot(
        cash="0",
        positions=(make_position(quantity="0", current_price="100"),),
        broker_total_value="0",
    )

    validate_broker_snapshot(snapshot)
    assert Decimal("0") == Decimal(0)
