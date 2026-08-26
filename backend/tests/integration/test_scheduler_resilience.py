"""Устойчивость цикла синхронизации (T045, FR-032, FR-033)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.broker.errors import BrokerUnavailableError
from financial_ai.db.settings_repo import set_interval_seconds
from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.scheduler import SyncScheduler
from financial_ai.sync.service import SyncResult, SyncService
from tests.fakes.fake_broker import FakeBroker

pytestmark = pytest.mark.db


def _scheduler(broker: FakeBroker) -> SyncScheduler:
    single_flight: SingleFlight[SyncResult] = SingleFlight()
    return SyncScheduler(SyncService(broker), single_flight)


async def test_cycle_continues_after_broker_error(db_session: AsyncSession) -> None:
    await set_interval_seconds(db_session, 15)
    await db_session.commit()

    broker = FakeBroker(error=BrokerUnavailableError("брокер недоступен"))
    scheduler = _scheduler(broker)

    await scheduler.start()
    try:
        # Ошибка не должна останавливать цикл: планировщик остаётся живым.
        await asyncio.sleep(0.2)
        assert scheduler.is_running is True
        assert broker.calls >= 1
    finally:
        await scheduler.stop()

    await db_session.commit()
    row = (
        await db_session.execute(
            text("select last_status, failure_reason_code from broker_sync_state where id = 1")
        )
    ).one()

    assert row.last_status == "failed"
    assert row.failure_reason_code == "broker_unavailable"


async def test_cycles_do_not_overlap(db_session: AsyncSession) -> None:
    # Синхронизация дольше, чем интервал был бы, — новый цикл не должен
    # стартовать поверх незавершённого (FR-033).
    await set_interval_seconds(db_session, 15)
    await db_session.commit()

    broker = FakeBroker(delay=0.2)
    scheduler = _scheduler(broker)

    await scheduler.start()
    try:
        await asyncio.sleep(0.1)
        # Пока первая синхронизация идёт, второго обращения нет.
        assert broker.calls == 1
    finally:
        await scheduler.stop()


async def test_missed_intervals_do_not_pile_up(db_session: AsyncSession) -> None:
    await set_interval_seconds(db_session, 15)
    await db_session.commit()

    broker = FakeBroker(delay=0.3)
    scheduler = _scheduler(broker)

    await scheduler.start()
    await asyncio.sleep(0.35)
    await scheduler.stop()

    # Очередь пропущенных циклов не накапливается: обращений столько,
    # сколько успело выполниться.
    assert broker.calls <= 2


async def test_failed_sync_does_not_overwrite_stored_state(db_session: AsyncSession) -> None:
    ok_broker = FakeBroker()
    await SyncService(ok_broker).sync_account_state()

    await db_session.commit()
    before = (
        await db_session.execute(text("select total_value, captured_at from account_state"))
    ).one()

    failing = FakeBroker(error=BrokerUnavailableError("таймаут"))
    result = await SyncService(failing).sync_account_state()

    assert result.status == "failed"

    await db_session.commit()
    after = (
        await db_session.execute(text("select total_value, captured_at from account_state"))
    ).one()

    # US4 AS3: сохранённое состояние не затирается частичными или пустыми данными.
    assert after.total_value == before.total_value
    assert after.captured_at == before.captured_at
