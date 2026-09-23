"""Контракт публичных маршрутов рыночных данных — contracts/market-data-api.md.

Публичная граница интерфейса. Проверяется ровно то, ради чего она заведена:
интерфейс обращается только сюда, ответ сборщика доходит без искажений, а
недоступность сборщика не выдаётся за отказ операции.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from financial_ai.config import get_settings

pytestmark = pytest.mark.db

COVERAGE_URL = "http://localhost:8000/internal/coverage"
CATCHUP_URL = "http://localhost:8000/internal/catchup"

COVERAGE_PAYLOAD = {
    "asof_date": "2026-09-03",
    "catchup_window": {
        "date_from": "2026-04-20",
        "date_till": "2026-09-03",
        "sessions": 314,
    },
    "groups": [
        {
            "group": "quotes",
            "title": "котировки",
            "has_history": True,
            "window_sessions": 314,
            "sessions_covered": 255,
            "coverage_ratio": 0.812,
            "period_from": "2025-06-10",
            "period_till": "2026-09-03",
            "gaps": 59,
            "rows_total": None,
            "rows_with_values": None,
            "value_ratio": 0.961,
            "looks_collected_but_empty": False,
        },
        {
            "group": "reference",
            "title": "справочники",
            "has_history": False,
            "rows_total": 506,
            "rows_with_values": 506,
            "value_ratio": 1.0,
            "looks_collected_but_empty": False,
        },
    ],
}

RUNNING_PAYLOAD = {
    "status": "running",
    "groups": ["quotes", "aggregates"],
    "date_from": "2026-04-20",
    "date_till": "2026-09-03",
    "clamped": False,
    "requested": 90,
    "closed": 18,
    "failed": 1,
    "remaining": 71,
    "current": "2026-05-14",
    "started_at": "2026-09-10T09:12:04+00:00",
    "finished_at": None,
    "reason": None,
}


# --- сводка (FR-044, FR-047) --------------------------------------------------


async def test_coverage_reaches_the_interface_unchanged(api_client: httpx.AsyncClient) -> None:
    """Ответ сборщика доходит как есть: второй формы у сводки нет."""
    get_settings.cache_clear()

    with respx.mock(assert_all_called=True) as mock:
        mock.get(COVERAGE_URL).respond(json=COVERAGE_PAYLOAD)

        response = await api_client.get("/api/market-data/coverage")

    assert response.status_code == 200
    assert response.json() == COVERAGE_PAYLOAD


async def test_group_without_history_keeps_absent_fields_absent(
    api_client: httpx.AsyncClient,
) -> None:
    """Разница между «поля нет» и «поле равно null» значима (FR-014).

    Ноль или `null` вместо отсутствия читались бы как «ничего не собрано»,
    и справочник выглядел бы недобранным.
    """
    with respx.mock as mock:
        mock.get(COVERAGE_URL).respond(json=COVERAGE_PAYLOAD)

        payload = (await api_client.get("/api/market-data/coverage")).json()

    reference = next(row for row in payload["groups"] if row["group"] == "reference")
    assert "window_sessions" not in reference
    assert "coverage_ratio" not in reference

    # А вот отсутствующие абсолютные числа строк приходят именно как null:
    # интерфейс покажет прочерк, а не ноль (FR-015).
    quotes = next(row for row in payload["groups"] if row["group"] == "quotes")
    assert quotes["rows_total"] is None


async def test_coverage_is_not_cached(api_client: httpx.AsyncClient) -> None:
    """Возраст данных должен быть честным."""
    with respx.mock as mock:
        mock.get(COVERAGE_URL).respond(json=COVERAGE_PAYLOAD)

        response = await api_client.get("/api/market-data/coverage")

    assert response.headers["cache-control"] == "no-store"


async def test_empty_calendar_is_passed_through(api_client: httpx.AsyncClient) -> None:
    """Пустое хранилище — состояние системы, а не отказ публичной границы."""
    with respx.mock as mock:
        mock.get(COVERAGE_URL).respond(
            status_code=422,
            json={"detail": {"code": "calendar_empty", "message": "календарь пуст"}},
        )

        response = await api_client.get("/api/market-data/coverage")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "calendar_empty"


# --- состояние прогона (FR-035) -----------------------------------------------


async def test_catchup_state_reaches_the_interface_unchanged(
    api_client: httpx.AsyncClient,
) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(CATCHUP_URL).respond(json=RUNNING_PAYLOAD)

        response = await api_client.get("/api/market-data/catchup")

    assert response.status_code == 200
    payload = response.json()
    assert payload == RUNNING_PAYLOAD
    # Число сессий плана приходит с сервера и суммой пропусков не заменяется.
    assert payload["requested"] == 90
    assert payload["remaining"] == payload["requested"] - payload["closed"] - payload["failed"]


# --- запуск (FR-019, FR-020, FR-038) ------------------------------------------


async def test_start_passes_groups_and_range(api_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(CATCHUP_URL).respond(
            json={
                "status": "running",
                "groups": ["quotes"],
                "date_from": "2026-06-01",
                "date_till": "2026-08-28",
                "clamped": False,
                "requested_sessions": 42,
            }
        )

        response = await api_client.post(
            "/api/market-data/catchup",
            json={"groups": ["quotes"], "date_from": "2026-06-01", "date_till": "2026-08-28"},
        )

    assert response.status_code == 200
    assert response.json()["requested_sessions"] == 42

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"groups": ["quotes"], "date_from": "2026-06-01", "date_till": "2026-08-28"}


async def test_start_without_parameters_sends_an_empty_body(
    api_client: httpx.AsyncClient,
) -> None:
    """Умолчания принадлежат сборщику: пустые поля наружу не выдумываются."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(CATCHUP_URL).respond(
            json={"status": "running", "groups": [], "requested_sessions": 90, "clamped": False}
        )

        await api_client.post("/api/market-data/catchup", json={})

    assert json.loads(route.calls.last.request.content) == {}


async def test_просьба_продолжить_доходит_до_сборщика(
    api_client: httpx.AsyncClient,
) -> None:
    """Продолжение — просьба человека, и граница передаёт её как есть (FR-058)."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(CATCHUP_URL).respond(
            json={
                "status": "running",
                "groups": [],
                "requested_sessions": 3,
                "clamped": False,
                "resumed": True,
            }
        )

        response = await api_client.post("/api/market-data/catchup", json={"resume": True})

    assert json.loads(route.calls.last.request.content) == {"resume": True}
    assert response.json()["resumed"] is True


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (409, "catchup_already_running"),
        (422, "unknown_group"),
        (422, "invalid_range"),
    ],
)
async def test_refusals_keep_their_own_reason(
    api_client: httpx.AsyncClient, status_code: int, code: str
) -> None:
    """Каждая причина отказа доходит своей: одна за другую не выдаётся."""
    with respx.mock as mock:
        mock.post(CATCHUP_URL).respond(
            status_code=status_code, json={"detail": {"code": code, "message": "отказ"}}
        )

        response = await api_client.post("/api/market-data/catchup", json={})

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code


async def test_nothing_to_catch_up_is_not_an_error(api_client: httpx.AsyncClient) -> None:
    """Полнота — спокойное подтверждение, а не отказ."""
    with respx.mock as mock:
        mock.post(CATCHUP_URL).respond(
            json={"status": "idle", "requested_sessions": 0, "reason": "пропущенных сессий нет"}
        )

        response = await api_client.post("/api/market-data/catchup", json={})

    assert response.status_code == 200
    assert response.json()["requested_sessions"] == 0


# --- остановка (FR-023, FR-032) -----------------------------------------------


async def test_stop_returns_the_requested_state(api_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.delete(CATCHUP_URL).respond(json={"status": "stopping", "current": "2026-05-14"})

        response = await api_client.delete("/api/market-data/catchup")

    assert response.status_code == 200
    # Ответ означает, что остановка запрошена, а не что она состоялась.
    assert response.json()["status"] == "stopping"


# --- недоступность сборщика (FR-042, FR-047b) ---------------------------------


@pytest.mark.parametrize("path", ["/api/market-data/coverage", "/api/market-data/catchup"])
async def test_unreachable_worker_is_not_a_refusal(
    api_client: httpx.AsyncClient, path: str
) -> None:
    """Сборщик недоступен — это не отказ операции и не обрыв связи с сервером.

    Интерфейс на этом основании помечает показанное как последнее известное и
    не выдаёт его за подтверждённый процесс.
    """
    with respx.mock as mock:
        mock.get(url__startswith="http://localhost:8000/internal").mock(
            side_effect=httpx.ConnectError("нет соединения")
        )

        response = await api_client.get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"


async def test_worker_error_text_does_not_reach_the_interface(
    api_client: httpx.AsyncClient,
) -> None:
    """В сообщении нет ни внутреннего адреса, ни текста исключения (FR-043)."""
    with respx.mock as mock:
        mock.delete(CATCHUP_URL).mock(
            side_effect=httpx.ConnectError("[Errno 111] Connection refused to backend-worker:8000")
        )

        response = await api_client.delete("/api/market-data/catchup")

    assert response.status_code == 503
    assert "backend-worker" not in response.text
    assert "Errno" not in response.text
