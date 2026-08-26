"""Успешная синхронизация: атомарная запись состояния (T027, FR-008)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.sync.service import SyncService
from tests.fakes.fake_broker import FakeBroker, make_position, make_snapshot

pytestmark = pytest.mark.db


async def test_snapshot_is_stored_with_positions(db_session: AsyncSession) -> None:
    broker = FakeBroker(
        make_snapshot(
            cash="40545",
            positions=(
                make_position(instrument_uid="uid-sber", ticker="SBER", quantity="1200"),
                make_position(
                    instrument_uid="uid-lkoh",
                    ticker="LKOH",
                    name="ЛУКОЙЛ",
                    quantity="10",
                    average_price="6800",
                    current_price="7100",
                ),
            ),
        )
    )

    result = await SyncService(broker).sync_account_state()

    assert result.ok
    assert broker.calls == 1

    await db_session.commit()
    state = (
        await db_session.execute(
            text("select total_value, cash, positions_count from account_state where id = 1")
        )
    ).one()

    assert state.positions_count == 2
    assert state.cash == Decimal("40545")
    # Сумма позиций и денежных средств: 362064 + 71000 + 40545.
    assert state.total_value == Decimal("473609")

    positions = (
        await db_session.execute(
            text("select instrument_uid, value, unrealized_pnl from portfolio_position "
                 "order by sort_order")
        )
    ).all()

    assert [p.instrument_uid for p in positions] == ["uid-sber", "uid-lkoh"]
    assert positions[1].value == Decimal("71000")
    assert positions[1].unrealized_pnl == Decimal("3000")


async def test_nano_precision_survives_storage(db_session: AsyncSession) -> None:
    broker = FakeBroker(
        make_snapshot(
            cash="0.000000001",
            positions=(
                make_position(
                    quantity="1",
                    average_price="0.000000001",
                    current_price="0.000000002",
                ),
            ),
        )
    )

    assert (await SyncService(broker).sync_account_state()).ok

    await db_session.commit()
    row = (
        await db_session.execute(text("select cash, total_value from account_state where id = 1"))
    ).one()

    # NUMERIC(28,9) обязан сохранить nano без потерь (SC-002).
    assert row.cash == Decimal("0.000000001")
    assert row.total_value == Decimal("0.000000003")


async def test_successful_sync_marks_broker_connected(db_session: AsyncSession) -> None:
    assert (await SyncService(FakeBroker()).sync_account_state()).ok

    await db_session.commit()
    row = (
        await db_session.execute(
            text("select broker_status, last_status, last_success_at, consecutive_failures "
                 "from broker_sync_state where id = 1")
        )
    ).one()

    assert row.broker_status == "connected"
    assert row.last_status == "ok"
    assert row.last_success_at is not None
    assert row.consecutive_failures == 0


async def test_repeated_sync_replaces_positions_entirely(db_session: AsyncSession) -> None:
    first = FakeBroker(
        make_snapshot(
            positions=(
                make_position(instrument_uid="uid-a"),
                make_position(instrument_uid="uid-b"),
            )
        )
    )
    await SyncService(first).sync_account_state()

    second = FakeBroker(make_snapshot(positions=(make_position(instrument_uid="uid-c"),)))
    await SyncService(second).sync_account_state()

    await db_session.commit()
    uids = [
        row.instrument_uid
        for row in (
            await db_session.execute(text("select instrument_uid from portfolio_position"))
        ).all()
    ]

    # Инструменты, исчезнувшие из портфеля, не остаются в таблице.
    assert uids == ["uid-c"]


async def test_account_is_stored_masked_only(db_session: AsyncSession) -> None:
    await SyncService(FakeBroker()).sync_account_state()

    await db_session.commit()
    row = (
        await db_session.execute(
            text("select masked_id, display_name from investment_account where id = 1")
        )
    ).one()

    assert row.masked_id == "•• 3456"
    assert row.display_name == "Основной брокерский счёт"


async def test_empty_portfolio_is_stored_as_zeroes(db_session: AsyncSession) -> None:
    broker = FakeBroker(make_snapshot(cash="0", positions=()))

    assert (await SyncService(broker).sync_account_state()).ok

    await db_session.commit()
    row = (
        await db_session.execute(
            text("select total_value, positions_count, positions_cost_basis from account_state "
                 "where id = 1")
        )
    ).one()

    assert row.total_value == Decimal("0")
    assert row.positions_count == 0
    assert row.positions_cost_basis == Decimal("0")
