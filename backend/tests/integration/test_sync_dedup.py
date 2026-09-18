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

    broker = FakeBroker(delay=0.5)
    scheduler = _scheduler(broker)

    # Ждём, пока блокировка свободна, а не утверждаем это сразу: она живёт в
    # соединении, и предыдущий тест мог ещё не успеть его вернуть. Проверка
    # здесь про то, что блокировку ВИДНО во время синхронизации, а не про
    # чужую уборку — мигала она именно на этой строке.
    await _wait_for_lock(db_session, held=False)

    task = asyncio.create_task(scheduler.run_once())

    # Backend-API видит выполняющуюся синхронизацию, хотя лок берёт Worker.
    await _wait_for_lock(db_session, held=True)

    await task
    await _wait_for_lock(db_session, held=False)


async def _wait_for_lock(session: AsyncSession, *, held: bool, timeout: float = 5.0) -> None:
    """Дождаться состояния блокировки и упасть с внятной причиной.

    Ожидание вместо мгновенной проверки: блокировку берёт и отпускает другая
    задача, и момент перехода не наступает синхронно с нашей строкой.
    """
    from financial_ai.sync import advisory

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await advisory.is_held(session) is held:
            return
        await asyncio.sleep(0.01)

    raise AssertionError(f"блокировка не перешла в состояние held={held} за {timeout:g} с")
