"""Контракт GET /api/portfolio (T021).

Проверяется структура ответа по contracts/backend-api.md: состояния раздела
должны выводиться из ответа однозначно, а денежные значения приходить
строками.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from financial_ai.sync.service import SyncService
from tests.fakes.fake_broker import FakeBroker, make_position, make_snapshot

pytestmark = pytest.mark.db


async def test_returns_null_snapshot_before_first_sync(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/portfolio")

    assert response.status_code == 200
    payload = response.json()

    # Успешной синхронизации ещё не было: нули за фактические данные
    # не выдаются (US4 AS4).
    assert payload["snapshot"] is None
    assert payload["broker"]["status"] == "not_configured"
    assert payload["broker"]["account"] is None
    assert payload["sync"]["status"] == "failed"


async def test_returns_stored_state(api_client: httpx.AsyncClient) -> None:
    await SyncService(FakeBroker()).sync_account_state()

    payload = (await api_client.get("/api/portfolio")).json()
    snapshot = payload["snapshot"]

    assert payload["broker"]["status"] == "connected"
    assert payload["broker"]["account"]["masked_id"] == "•• 3456"
    assert snapshot["positions_count"] == 1
    assert snapshot["age_seconds"] >= 0
    assert payload["sync"]["status"] == "ok"
    assert payload["sync"]["is_stale"] is False
    assert payload["sync"]["refresh_interval_seconds"] == 60
    assert payload["sync"]["stale_after_seconds"] == 180


async def test_money_values_are_strings(api_client: httpx.AsyncClient) -> None:
    await SyncService(FakeBroker()).sync_account_state()

    snapshot = (await api_client.get("/api/portfolio")).json()["snapshot"]
    position = snapshot["positions"][0]

    for field in ("total_value", "cash", "cash_share", "unrealized_pnl"):
        assert isinstance(snapshot[field], str), field

    for field in ("quantity", "average_price", "current_price", "value", "share"):
        assert isinstance(position[field], str), field


async def test_totals_reconcile_in_response(api_client: httpx.AsyncClient) -> None:
    await SyncService(
        FakeBroker(
            make_snapshot(
                cash="10000",
                positions=(
                    make_position(instrument_uid="a", quantity="10", current_price="100"),
                    make_position(instrument_uid="b", quantity="5", current_price="200"),
                ),
            )
        )
    ).sync_account_state()

    snapshot = (await api_client.get("/api/portfolio")).json()["snapshot"]

    total = Decimal(snapshot["total_value"])
    positions_value = sum(Decimal(p["value"]) for p in snapshot["positions"])

    # SC-003: суммы сходятся, доли дают 100%.
    assert total == positions_value + Decimal(snapshot["cash"])

    shares = [Decimal(p["share"]) for p in snapshot["positions"]]
    shares.append(Decimal(snapshot["cash_share"]))
    assert sum(shares) == Decimal("1")


async def test_unknown_percent_is_null(api_client: httpx.AsyncClient) -> None:
    await SyncService(
        FakeBroker(
            make_snapshot(
                cash="0",
                positions=(make_position(average_price=None, current_price="50"),),
            )
        )
    ).sync_account_state()

    snapshot = (await api_client.get("/api/portfolio")).json()["snapshot"]

    assert snapshot["positions"][0]["unrealized_pnl"] is None
    assert snapshot["positions"][0]["unrealized_pnl_percent"] is None
    assert snapshot["unrealized_pnl_percent"] is None


async def test_position_without_ticker_keeps_instrument_id(api_client: httpx.AsyncClient) -> None:
    await SyncService(
        FakeBroker(
            make_snapshot(
                positions=(make_position(instrument_uid="uid-only", ticker=None, name=None),)
            )
        )
    ).sync_account_state()

    position = (await api_client.get("/api/portfolio")).json()["snapshot"]["positions"][0]

    assert position["instrument_uid"] == "uid-only"
    assert position["ticker"] is None
    assert position["name"] is None


async def test_response_is_not_cached(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/portfolio")

    # Возраст данных должен быть честным (FR-014).
    assert response.headers["cache-control"] == "no-store"


async def test_empty_portfolio_has_zero_totals(api_client: httpx.AsyncClient) -> None:
    await SyncService(FakeBroker(make_snapshot(cash="0", positions=()))).sync_account_state()

    snapshot = (await api_client.get("/api/portfolio")).json()["snapshot"]

    assert snapshot["positions"] == []
    assert Decimal(snapshot["total_value"]) == Decimal("0")
    # Доля денежных средств при нулевом итоге — ноль, без деления на ноль.
    assert Decimal(snapshot["cash_share"]) == Decimal("0")
