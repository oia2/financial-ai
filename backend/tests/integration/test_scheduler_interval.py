"""Интервал автообновления применяется без перезапуска (T044, SC-012)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.db.settings_repo import get_interval_seconds, set_interval_seconds
from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.scheduler import SyncScheduler
from financial_ai.sync.service import SyncResult, SyncService
from tests.fakes.fake_broker import FakeBroker

pytestmark = pytest.mark.db


async def _wait_for(condition, timeout: float = 3.0) -> None:
    """Ждёт выполнения условия, не полагаясь на фиксированные паузы."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("условие не выполнилось за отведённое время")


def _scheduler(broker: FakeBroker) -> SyncScheduler:
    single_flight: SingleFlight[SyncResult] = SingleFlight()
    return SyncScheduler(SyncService(broker), single_flight)


async def test_scheduler_syncs_without_user_action(db_session: AsyncSession) -> None:
    await set_interval_seconds(db_session, 15)
    await db_session.commit()

    broker = FakeBroker()
    scheduler = _scheduler(broker)
    await scheduler.start()
    try:
        await _wait_for(lambda: broker.calls >= 1)
    finally:
        await scheduler.stop()

    assert broker.calls >= 1


async def test_interval_is_read_from_database_each_cycle(db_session: AsyncSession) -> None:
    await set_interval_seconds(db_session, 3600)
    await db_session.commit()

    broker = FakeBroker()
    scheduler = _scheduler(broker)
    await scheduler.start()
    try:
        await _wait_for(lambda: scheduler.current_interval_seconds == 3600)
        assert scheduler.current_interval_seconds == 3600
    finally:
        await scheduler.stop()


async def test_changed_interval_applies_to_next_cycle(db_session: AsyncSession) -> None:
    # Первый цикл длинный: планировщик успевает уснуть с прежним значением.
    await set_interval_seconds(db_session, 15)
    await db_session.commit()

    broker = FakeBroker()
    scheduler = _scheduler(broker)
    await scheduler.start()
    try:
        await _wait_for(lambda: scheduler.current_interval_seconds == 15)

        await set_interval_seconds(db_session, 20)
        await db_session.commit()

        # Никакого перезапуска: следующий цикл сам прочитает новое значение.
        assert await get_interval_seconds(db_session) == 20
    finally:
        await scheduler.stop()


async def test_stop_waits_for_current_sync(db_session: AsyncSession) -> None:
    broker = FakeBroker(delay=0.15)
    scheduler = _scheduler(broker)

    await scheduler.start()
    await _wait_for(lambda: broker.calls >= 1)
    await scheduler.stop()

    # Цикл остановлен корректно, задача завершена.
    assert scheduler.is_running is False


async def test_scheduler_reports_running_state(db_session: AsyncSession) -> None:
    scheduler = _scheduler(FakeBroker())

    assert scheduler.is_running is False
    await scheduler.start()
    assert scheduler.is_running is True
    await scheduler.stop()
    assert scheduler.is_running is False
