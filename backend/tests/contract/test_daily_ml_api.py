"""Контракт публичных маршрутов жизненного цикла — contracts/daily-ml-lifecycle-api.md.

Публичная граница раздела «Ранжирование». Проверяется то, ради чего она
заведена: состояние и история читаются из хранилища, команды передаются
сборщику, а его недоступность не выдаётся за отказ операции.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.daily_ml.models import RunStatus
from financial_ai.daily_ml.repository import DailyMlRepository, RunInput

pytestmark = pytest.mark.db

WORKER = "http://localhost:8000/internal"
ASOF = dt.date(2026, 9, 11)


async def _run(
    session: AsyncSession,
    status: str = RunStatus.SUCCESS.value,
    digest: str = "sha256:набор",
    asof: dt.date = ASOF,
) -> int:
    repository = DailyMlRepository(session)
    run, _ = await repository.enqueue(
        RunInput(asof, digest, "daily-ml-emulator", "emulator-v1"),
        "file:///datasets/нет-такого",
        window=(asof - dt.timedelta(days=400), asof),
        input_complete=True,
    )
    now = dt.datetime.now(dt.UTC)

    if status == RunStatus.SUCCESS.value:
        await repository.mark_running(run.id, now)
        await repository.mark_success(
            run.id, now, [(1, "EQ_AST_SBER", "EQ_PRS_SBER", Decimal("0.5"))], emulated=True
        )
    elif status == RunStatus.FAILED.value:
        await repository.mark_running(run.id, now)
        await repository.mark_failed(run.id, now, "ranking_unavailable", "звено недоступно")

    await session.commit()
    return run.id


# --- состояние ----------------------------------------------------------------


async def test_status_separates_data_and_model_dates(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Две даты — данных и модели — это две разные величины."""
    await _run(db_session)

    with respx.mock:
        respx.get(f"{WORKER}/daily-ml/state").respond(
            json={"paused": False, "latest_data_ready": ASOF.isoformat(), "data_gap_sessions": 0}
        )
        response = await api_client.get("/api/daily-ml/status")

    assert response.status_code == 200
    body = response.json()
    # Готовность данных считает сборщик: перечень обязательных групп задаётся
    # его конфигурацией, и второй счёт разошёлся бы с первым.
    assert body["latest_data_ready"] == ASOF.isoformat()
    assert body["latest_ml_success"] == ASOF.isoformat()
    assert body["readiness_known"] is True


async def test_status_names_the_groups_that_block_readiness(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Сказано, каких данных не хватает, а не только что их ждут.

    «Ожидаются данные» без ответа «каких?» оставляет человека гадать, а расчёт
    готовности этот ответ уже содержит.
    """
    with respx.mock:
        respx.get(f"{WORKER}/daily-ml/state").respond(
            json={
                "paused": False,
                "latest_data_ready": None,
                "data_gap_sessions": 0,
                "blocking_groups": [
                    {"group": "aggregates", "title": "агрегаты"},
                    {"group": "positions", "title": "позиции по фьючерсам"},
                ],
            }
        )
        body = (await api_client.get("/api/daily-ml/status")).json()

    assert [item["group"] for item in body["blocking_groups"]] == ["aggregates", "positions"]
    assert body["blocking_groups"][0]["title"] == "агрегаты"


async def test_status_does_not_rebuild_the_dataset(
    api_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Состояние не пересобирает набор: оно опрашивается.

    Ответ на «изменился ли вход» стоит пересборки набора — на живом стенде это
    17 секунд процессорного времени. Работа упирается в процессор и держит весь
    процесс: при опросе раз в три секунды очередь вставала целиком, `/api/health`
    отвечал двадцать секунд, а состояние не укладывалось в таймаут. Ответ
    приходит от сборщика, который уже собрал набор на своём тике.
    """
    from financial_ai.ranking import dataset as dataset_module

    await _run(db_session)

    async def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("состояние пересобирает набор — это кладёт backend-api")

    monkeypatch.setattr(dataset_module, "build_dataset", forbidden)

    with respx.mock:
        respx.get(f"{WORKER}/daily-ml/state").respond(
            json={"paused": False, "latest_data_ready": ASOF.isoformat(), "stale_latest": True}
        )
        body = (await api_client.get("/api/daily-ml/status")).json()

    assert body["stale_latest"] is True


async def test_status_is_not_cached(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    with respx.mock:
        respx.get(f"{WORKER}/daily-ml/state").respond(json={"paused": False})
        response = await api_client.get("/api/daily-ml/status")

    assert response.headers["cache-control"] == "no-store"


async def test_status_carries_next_check_but_not_inference_start(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Время следующей ПРОВЕРКИ известно, время начала расчёта — нет.

    Поля для начала расчёта в ответе нет намеренно: оно зависит от готовности
    внешних данных, и показать его значило бы выдумать.
    """
    with respx.mock:
        respx.get(f"{WORKER}/daily-ml/state").respond(json={"paused": False})
        body = (await api_client.get("/api/daily-ml/status")).json()

    assert body["next_check_at"]
    assert "next_inference_at" not in body


async def test_status_survives_unavailable_worker(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Сборщик молчит — история всё равно читается из хранилища.

    Но «готовой даты нет» и «готовность неизвестна» — разные утверждения, и
    второе не выдаётся за первое.
    """
    await _run(db_session)

    with respx.mock:
        respx.get(f"{WORKER}/daily-ml/state").mock(side_effect=httpx.ConnectError("нет соединения"))
        response = await api_client.get("/api/daily-ml/status")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_ml_success"] == ASOF.isoformat()
    assert body["latest_data_ready"] is None
    assert body["readiness_known"] is False


# --- история ------------------------------------------------------------------


async def test_history_is_paged_and_filtered(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _run(db_session, RunStatus.SUCCESS.value, "sha256:a", ASOF)
    await _run(db_session, RunStatus.FAILED.value, "sha256:b", ASOF - dt.timedelta(days=1))

    all_runs = (await api_client.get("/api/daily-ml/runs")).json()
    assert all_runs["total"] == 2

    failed = (await api_client.get("/api/daily-ml/runs", params={"status": "failed"})).json()
    assert failed["total"] == 1
    assert failed["items"][0]["status"] == "failed"

    first_page = (await api_client.get("/api/daily-ml/runs", params={"limit": 1})).json()
    assert len(first_page["items"]) == 1


async def test_run_detail_carries_input_outcome_and_result(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    run_id = await _run(db_session)

    body = (await api_client.get(f"/api/daily-ml/runs/{run_id}")).json()

    assert body["input"]["dataset_digest"] == "sha256:набор"
    assert body["status"] == "success"
    assert body["items"][0]["asset_id"] == "EQ_AST_SBER"
    # Скор строкой: он участвует в сортировке, float исказил бы порядок.
    assert isinstance(body["items"][0]["score"], str)


async def test_detail_shows_stored_window_completeness_and_emulation(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Свойства прошлого читаются из прогона, а не считаются заново.

    Пересчёт по нынешним настройкам и нынешним данным ответил бы на сегодняшний
    вопрос вчерашней записью: глубина окна — настройка, полнота входа меняется
    с приходом данных, а звено однажды перестанет быть эмулятором.
    """
    run_id = await _run(db_session)

    body = (await api_client.get(f"/api/daily-ml/runs/{run_id}")).json()

    assert body["input"]["window_till"] == ASOF.isoformat()
    assert body["input"]["window_from"] == (ASOF - dt.timedelta(days=400)).isoformat()
    assert body["input"]["complete"] is True
    assert body["emulated"] is True


async def test_deleted_dataset_is_visible_in_the_detail(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Набор удалён ретеншеном: результат остаётся, повтор невозможен."""
    run_id = await _run(db_session)

    body = (await api_client.get(f"/api/daily-ml/runs/{run_id}")).json()

    assert body["input"]["dataset_available"] is False


async def test_unknown_run_is_404(api_client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    response = await api_client.get("/api/daily-ml/runs/999999")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"


# --- команды ------------------------------------------------------------------


async def test_reconcile_reports_nothing_to_do(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """«Проверить сейчас» при актуальном состоянии — ноль заданий."""
    with respx.mock:
        respx.post(f"{WORKER}/daily-ml/reconcile").respond(
            json={"queued": 0, "already_up_to_date": True, "notes": []}
        )
        body = (await api_client.post("/api/daily-ml/reconcile")).json()

    assert body["queued"] == 0
    assert body["already_up_to_date"] is True


async def test_pause_is_passed_to_the_worker(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    with respx.mock:
        route = respx.put(f"{WORKER}/daily-ml/settings").respond(json={"paused": True})
        body = (await api_client.put("/api/daily-ml/settings", json={"paused": True})).json()

    assert body["paused"] is True
    assert route.called


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("run_not_failed", 409),
        ("dataset_expired", 409),
        ("attempts_exhausted", 409),
        ("run_not_found", 404),
    ],
)
async def test_retry_refusals_keep_their_reason(
    api_client: httpx.AsyncClient, db_session: AsyncSession, code: str, expected_status: int
) -> None:
    """Каждая причина отказа в повторе доходит своей."""
    with respx.mock:
        respx.post(f"{WORKER}/daily-ml/runs/1/retry").respond(json={"error": code})
        response = await api_client.post("/api/daily-ml/runs/1/retry")

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == code


async def test_unavailable_worker_is_not_a_refusal(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Сборщик недоступен — это не отказ операции."""
    with respx.mock:
        respx.post(f"{WORKER}/daily-ml/reconcile").mock(
            side_effect=httpx.ConnectError("нет соединения")
        )
        response = await api_client.post("/api/daily-ml/reconcile")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"
    assert "ConnectError" not in response.text
