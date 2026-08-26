"""Сериализация ответов API (T025).

Decimal обязан уходить строкой без экспоненциальной записи: JSON-число в
JavaScript — это double, и на длинных портфелях он теряет копейки (SC-002).
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from financial_ai.api.schemas import PositionOut, SnapshotOut, SyncOut


def _position(**overrides: object) -> PositionOut:
    payload: dict[str, object] = {
        "instrument_uid": "uid-sber",
        "ticker": "SBER",
        "name": "Сбербанк",
        "asset_type": "share",
        "currency": "RUB",
        "quantity": Decimal("1200"),
        "average_price": Decimal("281.4"),
        "current_price": Decimal("301.72"),
        "value": Decimal("362064"),
        "unrealized_pnl": Decimal("24384"),
        "unrealized_pnl_percent": Decimal("0.0722"),
        "share": Decimal("0.899287"),
    }
    payload.update(overrides)
    return PositionOut(**payload)  # type: ignore[arg-type]


def test_decimal_fields_are_serialised_as_strings() -> None:
    dumped = _position().model_dump(mode="json")

    assert dumped["value"] == "362064"
    assert isinstance(dumped["quantity"], str)
    assert isinstance(dumped["current_price"], str)


def test_no_scientific_notation_for_small_values() -> None:
    dumped = _position(share=Decimal("0.000000001")).model_dump(mode="json")

    assert dumped["share"] == "0.000000001"
    assert "E" not in dumped["share"] and "e" not in dumped["share"]


def test_no_scientific_notation_for_large_values() -> None:
    dumped = _position(value=Decimal("1E+12")).model_dump(mode="json")

    assert dumped["value"] == "1000000000000"


def test_precision_survives_json_round_trip() -> None:
    exact = Decimal("362064.123456789")
    dumped = json.loads(_position(value=exact).model_dump_json())

    assert Decimal(dumped["value"]) == exact


def test_unknown_percent_is_null_not_zero() -> None:
    dumped = _position(unrealized_pnl=None, unrealized_pnl_percent=None).model_dump(mode="json")

    # Ложный ноль недопустим: процент не определён.
    assert dumped["unrealized_pnl_percent"] is None
    assert dumped["unrealized_pnl"] is None


def test_snapshot_serialises_money_as_strings() -> None:
    snapshot = SnapshotOut(
        captured_at=dt.datetime(2026, 8, 26, 11, 32, 18, tzinfo=dt.UTC),
        age_seconds=42,
        total_value=Decimal("402609"),
        cash=Decimal("40545"),
        cash_share=Decimal("0.100704"),
        unrealized_pnl=Decimal("4590"),
        unrealized_pnl_percent=Decimal("0.0129"),
        positions_count=1,
        positions=[_position()],
    )

    dumped = snapshot.model_dump(mode="json")

    assert dumped["total_value"] == "402609"
    assert dumped["cash_share"] == "0.100704"
    assert dumped["captured_at"].startswith("2026-08-26T11:32:18")


def test_sync_block_carries_reason_code_not_broker_text() -> None:
    sync = SyncOut(
        status="failed",
        last_success_at=None,
        last_attempt_at=None,
        failure_reason_code="broker_unavailable",
        is_stale=True,
        stale_after_seconds=180,
        refresh_interval_seconds=60,
        in_progress=False,
    )

    dumped = sync.model_dump(mode="json")

    assert dumped["failure_reason_code"] == "broker_unavailable"
    assert dumped["stale_after_seconds"] == 180
