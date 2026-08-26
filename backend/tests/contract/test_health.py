"""Контрактные тесты health-эндпоинтов (T020)."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.db


async def test_api_health_reports_ok(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_worker_health_reports_token_presence_not_value(
    worker_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "t.super-secret-token-value"
    monkeypatch.setenv("TBANK_INVEST_READ_TOKEN", secret)

    from financial_ai.config import get_settings

    get_settings.cache_clear()
    try:
        response = await worker_client.get("/internal/health")
    finally:
        get_settings.cache_clear()

    body = response.text
    payload = response.json()

    assert payload["broker_token"] == "configured"
    # Факт наличия — да, значение — никогда (FR-023, SC-009).
    assert secret not in body


async def test_worker_health_reports_missing_token(
    worker_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TBANK_INVEST_READ_TOKEN", raising=False)

    from financial_ai.config import get_settings

    get_settings.cache_clear()
    try:
        response = await worker_client.get("/internal/health")
    finally:
        get_settings.cache_clear()

    assert response.json()["broker_token"] == "missing"
