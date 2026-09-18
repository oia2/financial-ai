"""Контракт управления догоном — specs/005-market-data-control/contracts/catchup-control.md.

Образец ответов взят у соседнего `/internal/sync`: обращение состоялось — значит
`200`, а исход описан телом. Отличие одно и оно проверяется отдельно: повторный
запуск при идущем догоне **отклоняется**, а не присоединяется к идущему.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.runner import CatchupRunner

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
    dt.date(2026, 9, 1),
]
ASOF = SESSIONS[-1]


class FakeCatchUp:
    """Подделка сбора: проверяется управление, а не источники."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.visited: list[dt.date] = []

    async def __call__(self, session, settings, asof_date, *args, **kwargs):  # type: ignore[no-untyped-def]
        import asyncio

        result = ingest.CatchupResult()
        result.requested = list(kwargs.get("sessions") or [])
        should_stop = kwargs.get("should_stop")
        on_start = kwargs.get("on_session_start")
        on_done = kwargs.get("on_session_done")

        for day in result.requested:
            if should_stop is not None and should_stop():
                break
            if on_start is not None:
                on_start(day)
            if self.delay:
                await asyncio.sleep(self.delay)
            self.visited.append(day)
            result.closed.append(day)
            if on_done is not None:
                on_done(day, True)
        return result


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("312.4"),
        high=Decimal("315.1"),
        low=Decimal("311.0"),
        close=Decimal("314.22"),
        volume=Decimal("1000"),
    )


async def _seed(session: AsyncSession, collected: list[dt.date]) -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])
        await _mark_collected(repository, collected)
    await session.commit()


async def _mark_collected(repository: MarketDataRepository, days: list[dt.date]) -> None:
    """Отметить сессии собранными ПО ВСЕМ группам.

    Котировок мало: сессия закрыта, когда закрыт каждый источник каждой группы.
    Прежде план сбора считался по одним котировкам, и бар делал сессию
    «собранной» — из-за этого ручной догон брал в работу вчетверо меньше, чем
    было недобрано (FR-031, FR-032).
    """
    from financial_ai.market_data import groups as group_registry

    moment = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    for day in days:
        for group in group_registry.GROUPS:
            for source_id in group.source_ids:
                await repository.record_run(
                    run_id=f"seed-{day}-{source_id}",
                    source_id=source_id,
                    status="ok",
                    started_at=moment,
                    finished_at=moment,
                    session_date=day,
                    rows_written=1,
                )


def _install_runner(delay: float = 0.0) -> CatchupRunner:
    """Подменяет владельца задания на управляемого тестом."""
    from financial_ai.worker.app import app

    runner = CatchupRunner(Settings(market_data_catchup_window_sessions=len(SESSIONS)))
    app.state.catchup_runner = runner
    return runner


async def _wait_idle(runner: CatchupRunner) -> None:
    import asyncio

    for _ in range(200):
        if not runner.is_active:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("догон не завершился")


# --- POST /internal/catchup ---------------------------------------------------


async def test_start_returns_the_plan(
    worker_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp())
    runner = _install_runner()

    response = await worker_client.post("/internal/catchup", json={"groups": ["quotes"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["groups"] == ["quotes"]
    assert payload["requested_sessions"] == 4
    assert payload["clamped"] is False
    assert payload["date_from"] == SESSIONS[0].isoformat()

    await _wait_idle(runner)


async def test_nothing_to_catch_up_is_not_an_error(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Запускать нечего — тоже `200`: запрос состоялся."""
    await _seed(db_session, SESSIONS)
    _install_runner()

    response = await worker_client.post("/internal/catchup", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "idle"
    assert payload["requested_sessions"] == 0
    assert payload["reason"]


async def test_second_start_is_rejected_with_409(
    worker_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два одновременных сбора писали бы одно и то же и удваивали обращения."""
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp(delay=0.05))
    runner = _install_runner()

    first = await worker_client.post("/internal/catchup", json={})
    second = await worker_client.post("/internal/catchup", json={})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "catchup_already_running"

    runner.stop()
    await _wait_idle(runner)


async def test_empty_storage_answers_backfill_required(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Не ошибка ввода, а состояние системы: нужна первичная загрузка."""
    await _seed(db_session, [])
    _install_runner()

    response = await worker_client.post("/internal/catchup", json={})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "backfill_required"


async def test_unknown_group_is_422(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    _install_runner()

    response = await worker_client.post("/internal/catchup", json={"groups": ["котировки"]})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_group"


async def test_reversed_range_is_422(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    _install_runner()

    response = await worker_client.post(
        "/internal/catchup",
        json={"date_from": SESSIONS[3].isoformat(), "date_till": SESSIONS[0].isoformat()},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_range"


async def test_unparsable_date_is_422(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    _install_runner()

    response = await worker_client.post("/internal/catchup", json={"date_from": "вчера"})

    assert response.status_code == 422


async def test_clamped_range_is_visible_in_the_answer(
    worker_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Человек должен видеть, что его диапазон сузили."""
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp())
    runner = _install_runner()

    response = await worker_client.post(
        "/internal/catchup", json={"date_from": "2020-01-01", "date_till": "2030-01-01"}
    )

    assert response.json()["clamped"] is True
    await _wait_idle(runner)


# --- GET /internal/catchup ----------------------------------------------------


async def test_status_before_any_start_is_idle(worker_client: httpx.AsyncClient) -> None:
    """После перезапуска компонента ход предыдущего догона не виден."""
    _install_runner()

    response = await worker_client.get("/internal/catchup")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"


async def test_status_carries_progress_fields(
    worker_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp())
    runner = _install_runner()

    await worker_client.post("/internal/catchup", json={})
    await _wait_idle(runner)

    payload = (await worker_client.get("/internal/catchup")).json()
    assert payload["status"] == "finished"
    assert payload["requested"] == 4
    assert payload["closed"] == 4
    assert payload["remaining"] == 0
    assert payload["failed"] == 0
    assert payload["started_at"] is not None
    assert payload["finished_at"] is not None


# --- DELETE /internal/catchup -------------------------------------------------


async def test_stop_is_soft(
    worker_client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Текущая сессия доводится до конца, дальнейшие не начинаются."""
    await _seed(db_session, [SESSIONS[4]])
    fake = FakeCatchUp(delay=0.05)
    monkeypatch.setattr(ingest, "catch_up", fake)
    runner = _install_runner()

    await worker_client.post("/internal/catchup", json={})
    response = await worker_client.delete("/internal/catchup")

    assert response.status_code == 200
    assert response.json()["status"] == "stopping"

    await _wait_idle(runner)
    assert (await worker_client.get("/internal/catchup")).json()["status"] == "stopped"
    assert len(fake.visited) < 4


async def test_stop_with_nothing_running_is_not_an_error(
    worker_client: httpx.AsyncClient,
) -> None:
    """Запрос состоялся, останавливать оказалось нечего."""
    _install_runner()

    response = await worker_client.delete("/internal/catchup")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
