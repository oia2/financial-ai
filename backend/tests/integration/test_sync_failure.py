"""Неуспешная синхронизация не разрушает сохранённое состояние (T067)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.broker.errors import (
    BrokerRateLimitedError,
    BrokerTokenMissingError,
    BrokerTokenRejectedError,
    BrokerUnavailableError,
)
from financial_ai.sync.service import SyncService
from tests.fakes.fake_broker import FakeBroker, make_position, make_snapshot

pytestmark = pytest.mark.db


async def _sync_state(session: AsyncSession) -> object:
    await session.commit()
    return (
        await session.execute(
            text(
                "select broker_status, last_status, failure_reason_code, failure_detail, "
                "last_attempt_at, last_success_at, consecutive_failures "
                "from broker_sync_state where id = 1"
            )
        )
    ).one()


async def test_failure_updates_only_sync_state(db_session: AsyncSession) -> None:
    await SyncService(FakeBroker()).sync_account_state()
    await db_session.commit()
    before = (
        await db_session.execute(
            text("select total_value, cash, captured_at from account_state where id = 1")
        )
    ).one()
    positions_before = (
        await db_session.execute(text("select count(*) from portfolio_position"))
    ).scalar()

    result = await SyncService(
        FakeBroker(error=BrokerUnavailableError("таймаут"))
    ).sync_account_state()

    assert result.status == "failed"

    await db_session.commit()
    after = (
        await db_session.execute(
            text("select total_value, cash, captured_at from account_state where id = 1")
        )
    ).one()

    # US4 AS3: состояние не затирается частичными или пустыми данными.
    assert (after.total_value, after.cash, after.captured_at) == (
        before.total_value,
        before.cash,
        before.captured_at,
    )
    assert (
        await db_session.execute(text("select count(*) from portfolio_position"))
    ).scalar() == positions_before


async def test_failure_records_reason_and_attempt_time(db_session: AsyncSession) -> None:
    await SyncService(FakeBroker(error=BrokerUnavailableError("сеть"))).sync_account_state()

    row = await _sync_state(db_session)

    assert row.last_status == "failed"
    assert row.failure_reason_code == "broker_unavailable"
    assert row.last_attempt_at is not None
    assert row.last_success_at is None


async def test_consecutive_failures_are_counted(db_session: AsyncSession) -> None:
    broker = FakeBroker(error=BrokerUnavailableError("сеть"))
    for _ in range(3):
        await SyncService(broker).sync_account_state()

    row = await _sync_state(db_session)

    assert row.consecutive_failures == 3


async def test_success_resets_failure_counter(db_session: AsyncSession) -> None:
    await SyncService(FakeBroker(error=BrokerUnavailableError("сеть"))).sync_account_state()
    await SyncService(FakeBroker()).sync_account_state()

    row = await _sync_state(db_session)

    assert row.consecutive_failures == 0
    assert row.last_status == "ok"
    assert row.failure_reason_code is None
    assert row.broker_status == "connected"


async def test_rejected_token_sets_rejected_status(db_session: AsyncSession) -> None:
    await SyncService(
        FakeBroker(error=BrokerTokenRejectedError("токен отозван"))
    ).sync_account_state()

    row = await _sync_state(db_session)

    assert row.broker_status == "rejected"
    assert row.failure_reason_code == "broker_rejected_token"


async def test_missing_token_sets_not_configured_status(db_session: AsyncSession) -> None:
    await SyncService(
        FakeBroker(error=BrokerTokenMissingError("токен не задан"))
    ).sync_account_state()

    row = await _sync_state(db_session)

    # Пользователю сообщается, что доступ не сконфигурирован, а не отозван.
    assert row.broker_status == "not_configured"


async def test_rate_limit_does_not_change_broker_status(db_session: AsyncSession) -> None:
    await SyncService(FakeBroker()).sync_account_state()
    await SyncService(FakeBroker(error=BrokerRateLimitedError("лимит"))).sync_account_state()

    row = await _sync_state(db_session)

    # Лимит запросов не означает потерю доступа.
    assert row.broker_status == "connected"
    assert row.failure_reason_code == "rate_limited"


async def test_invalid_snapshot_is_not_stored(db_session: AsyncSession) -> None:
    # Несогласованные суммы: расчётный итог заметно расходится с итогом брокера.
    broker = FakeBroker(
        make_snapshot(
            cash="1000",
            positions=(make_position(quantity="1", current_price="1"),),
            broker_total_value="999999",
        )
    )

    result = await SyncService(broker).sync_account_state()

    assert result.status == "failed"
    assert result.failure_reason_code == "validation_failed"

    await db_session.commit()
    stored = (await db_session.execute(text("select count(*) from account_state"))).scalar()
    assert stored == 0
