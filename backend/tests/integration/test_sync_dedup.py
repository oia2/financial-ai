"""Дедупликация одновременных синхронизаций (T059, FR-029, FR-033).

Одновременно может выполняться только одна синхронизация состояния счёта.
Повторный запрос во время текущей не запускает второй broker request, а
использует результат уже выполняющейся операции.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.scheduler import SyncScheduler
from financial_ai.sync.service import SyncResult, SyncService
from tests.fakes.fake_broker import FakeBroker

pytestmark = pytest.mark.db


def _scheduler(broker: FakeBroker) -> SyncScheduler:
    single_flight: SingleFlight[SyncResult] = SingleFlight()
    return SyncScheduler(SyncService(broker), single_flight)


async def test_parallel_manual_requests_make_one_broker_call(db_session: AsyncSession) -> None:
    broker = FakeBroker(delay=0.15)
    scheduler = _scheduler(broker)

    first, second = await asyncio.gather(scheduler.run_once(), scheduler.run_once())

    # Ровно одно обращение к брокеру на два запроса.
    assert broker.calls == 1

    results = [first, second]
    assert all(result.status == "ok" for result, _ in results)
    # Один из запросов получил чужой результат.
    assert sorted(joined for _, joined in results) == [False, True]


async def test_deduplicated_request_returns_same_snapshot(db_session: AsyncSession) -> None:
    broker = FakeBroker(delay=0.1)
    scheduler = _scheduler(broker)

    (first, _), (second, _) = await asyncio.gather(scheduler.run_once(), scheduler.run_once())

    assert first.captured_at == second.captured_at


async def test_manual_request_during_background_cycle_is_deduplicated(
    db_session: AsyncSession,
) -> None:
    broker = FakeBroker(delay=0.25)
    scheduler = _scheduler(broker)

    await scheduler.start()
    try:
        # Фоновый цикл уже начал синхронизацию — ручной запрос обязан
        # присоединиться к ней, а не идти к брокеру повторно (US2 AS5).
        await asyncio.sleep(0.05)
        result, joined = await scheduler.run_once()

        assert joined is True
        assert result.status == "ok"
        assert broker.calls == 1
    finally:
        await scheduler.stop()


async def test_sequential_requests_are_not_deduplicated(db_session: AsyncSession) -> None:
    broker = FakeBroker()
    scheduler = _scheduler(broker)

    _, first_joined = await scheduler.run_once()
    _, second_joined = await scheduler.run_once()

    # Последовательные запросы — это два честных обновления.
    assert first_joined is False
    assert second_joined is False
    assert broker.calls == 2


async def test_in_progress_is_visible_through_advisory_lock(db_session: AsyncSession) -> None:
    from financial_ai.sync import advisory

    broker = FakeBroker(delay=0.5)
    scheduler = _scheduler(broker)

    assert await advisory.is_held(db_session) is False

    task = asyncio.create_task(scheduler.run_once())

    # Ждём появления блокировки, а не фиксированную паузу: под нагрузкой
    # старт задачи занимает разное время.
    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline:
        if await advisory.is_held(db_session):
            break
        await asyncio.sleep(0.01)

    # Backend-API видит выполняющуюся синхронизацию, хотя лок берёт Worker.
    assert await advisory.is_held(db_session) is True

    await task
    assert await advisory.is_held(db_session) is False
