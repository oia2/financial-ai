"""Контракт POST /api/portfolio/refresh (T057)."""

from __future__ import annotations

import httpx
import pytest
import respx

from financial_ai.config import get_settings

pytestmark = pytest.mark.db

WORKER_URL = "http://localhost:8000/internal/sync"


async def test_translates_worker_result(api_client: httpx.AsyncClient) -> None:
    get_settings.cache_clear()

    with respx.mock(assert_all_called=True) as mock:
        mock.post(WORKER_URL).respond(
            json={
                "status": "ok",
                "deduplicated": False,
                "captured_at": "2026-08-26T11:35:02+00:00",
                "failure_reason_code": None,
                "duration_ms": 640,
            }
        )

        response = await api_client.post("/api/portfolio/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["deduplicated"] is False
    assert payload["captured_at"].startswith("2026-08-26T11:35:02")


async def test_reports_broker_failure_without_broker_text(api_client: httpx.AsyncClient) -> None:
    with respx.mock as mock:
        mock.post(WORKER_URL).respond(
            json={
                "status": "failed",
                "deduplicated": False,
                "captured_at": None,
                "failure_reason_code": "broker_unavailable",
                "duration_ms": 120,
            }
        )

        payload = (await api_client.post("/api/portfolio/refresh")).json()

    assert payload["status"] == "failed"
    # Наружу уходит код причины, а не текст ответа брокера (FR-028).
    assert payload["failure_reason_code"] == "broker_unavailable"


async def test_marks_deduplicated_result(api_client: httpx.AsyncClient) -> None:
    with respx.mock as mock:
        mock.post(WORKER_URL).respond(
            json={
                "status": "ok",
                "deduplicated": True,
                "captured_at": "2026-08-26T11:35:02+00:00",
                "failure_reason_code": None,
                "duration_ms": 5,
            }
        )

        payload = (await api_client.post("/api/portfolio/refresh")).json()

    assert payload["deduplicated"] is True


async def test_returns_503_when_worker_unavailable(api_client: httpx.AsyncClient) -> None:
    with respx.mock as mock:
        mock.post(WORKER_URL).mock(side_effect=httpx.ConnectError("нет соединения"))

        response = await api_client.post("/api/portfolio/refresh")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"


async def test_returns_503_when_worker_errors(api_client: httpx.AsyncClient) -> None:
    with respx.mock as mock:
        mock.post(WORKER_URL).respond(status_code=500)

        response = await api_client.post("/api/portfolio/refresh")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"
