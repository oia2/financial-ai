"""Контракт настройки интервала автообновления (T043)."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.db

URL = "/api/settings/refresh-interval"


async def test_returns_current_interval_with_bounds(api_client: httpx.AsyncClient) -> None:
    payload = (await api_client.get(URL)).json()

    assert payload == {
        "interval_seconds": 60,
        "min_seconds": 15,
        "max_seconds": 3600,
        "default_seconds": 60,
    }


async def test_accepts_value_inside_range(api_client: httpx.AsyncClient) -> None:
    response = await api_client.put(URL, json={"interval_seconds": 120})

    assert response.status_code == 200
    assert response.json()["interval_seconds"] == 120
    # Значение сохраняется, а не живёт только в ответе.
    assert (await api_client.get(URL)).json()["interval_seconds"] == 120


@pytest.mark.parametrize("value", [15, 3600])
async def test_accepts_range_boundaries(api_client: httpx.AsyncClient, value: int) -> None:
    assert (await api_client.put(URL, json={"interval_seconds": value})).status_code == 200


@pytest.mark.parametrize("value", [14, 0, -60, 3601, 100000])
async def test_rejects_value_outside_range(api_client: httpx.AsyncClient, value: int) -> None:
    response = await api_client.put(URL, json={"interval_seconds": value})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "interval_out_of_range"
    assert detail["min_seconds"] == 15
    assert detail["max_seconds"] == 3600


async def test_rejected_value_keeps_previous_interval(api_client: httpx.AsyncClient) -> None:
    await api_client.put(URL, json={"interval_seconds": 300})

    await api_client.put(URL, json={"interval_seconds": 5})

    # US2 AS4: прежний интервал продолжает действовать.
    assert (await api_client.get(URL)).json()["interval_seconds"] == 300


@pytest.mark.parametrize("value", ["60", 60.5, None, "быстро"])
async def test_rejects_non_integer_values(api_client: httpx.AsyncClient, value: object) -> None:
    response = await api_client.put(URL, json={"interval_seconds": value})

    assert response.status_code == 422


async def test_interval_is_reflected_in_portfolio_response(api_client: httpx.AsyncClient) -> None:
    await api_client.put(URL, json={"interval_seconds": 300})

    sync = (await api_client.get("/api/portfolio")).json()["sync"]

    assert sync["refresh_interval_seconds"] == 300
    # Порог устаревания пересчитан: max(3 × 300, 180).
    assert sync["stale_after_seconds"] == 900
