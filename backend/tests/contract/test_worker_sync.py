"""Контракт POST /internal/sync (T058)."""

from __future__ import annotations

import httpx
import pytest

from financial_ai.broker.errors import BrokerUnavailableError
from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.scheduler import SyncScheduler
from financial_ai.sync.service import SyncResult, SyncService
from tests.fakes.fake_broker import FakeBroker

pytestmark = pytest.mark.db


def _install_scheduler(client: httpx.AsyncClient, broker: FakeBroker) -> SyncScheduler:
    """Подменяет планировщик приложения на управляемый тестом."""
    from financial_ai.worker.app import app

    single_flight: SingleFlight[SyncResult] = SingleFlight()
    scheduler = SyncScheduler(SyncService(broker), single_flight)
    app.state.scheduler = scheduler
    app.state.single_flight = single_flight
    return scheduler


async def test_successful_sync_returns_captured_at(worker_client: httpx.AsyncClient) -> None:
    broker = FakeBroker()
    _install_scheduler(worker_client, broker)

    response = await worker_client.post("/internal/sync")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["deduplicated"] is False
    assert payload["captured_at"] is not None
    assert payload["failure_reason_code"] is None
    assert payload["duration_ms"] >= 0
    assert broker.calls == 1


async def test_broker_failure_is_200_with_failed_status(worker_client: httpx.AsyncClient) -> None:
    broker = FakeBroker(error=BrokerUnavailableError("недоступен"))
    _install_scheduler(worker_client, broker)

    response = await worker_client.post("/internal/sync")

    # Обращение к worker'у состоялось: 5xx означал бы отказ самого worker'а.
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["failure_reason_code"] == "broker_unavailable"
    assert payload["captured_at"] is None
