"""Доступ к хранилищу состояния счёта.

Успешная синхронизация заменяет состояние целиком и только целиком; неуспешная
не трогает его вовсе, обновляя лишь статус попытки (FR-008, US4 AS3).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.db.models import (
    SINGLETON_ID,
    AccountState,
    BrokerSyncState,
    InvestmentAccount,
    PortfolioPosition,
)
from financial_ai.domain.models import AccountSnapshot, BrokerAccount


async def get_account(session: AsyncSession) -> InvestmentAccount | None:
    return await session.get(InvestmentAccount, SINGLETON_ID)


async def get_state(session: AsyncSession) -> AccountState | None:
    return await session.get(AccountState, SINGLETON_ID)


async def get_positions(session: AsyncSession) -> list[PortfolioPosition]:
    result = await session.execute(
        select(PortfolioPosition)
        .where(PortfolioPosition.state_id == SINGLETON_ID)
        .order_by(PortfolioPosition.sort_order)
    )
    return list(result.scalars().all())


async def get_sync_state(session: AsyncSession) -> BrokerSyncState:
    """Статус синхронизации. Строка создаётся первой миграцией."""
    sync_state = await session.get(BrokerSyncState, SINGLETON_ID)
    if sync_state is None:
        sync_state = BrokerSyncState(
            id=SINGLETON_ID,
            broker_status="not_configured",
            last_status="failed",
            consecutive_failures=0,
        )
        session.add(sync_state)
        await session.flush()
    return sync_state


async def save_snapshot(
    session: AsyncSession,
    account: BrokerAccount,
    snapshot: AccountSnapshot,
) -> None:
    """Сохраняет состояние счёта целиком, в одной транзакции (FR-008).

    Вызывающий код открывает транзакцию и коммитит её: частично записанное
    состояние не должно стать видимым ни при каких обстоятельствах.
    """
    stored_account = await session.get(InvestmentAccount, SINGLETON_ID)
    if stored_account is None:
        stored_account = InvestmentAccount(id=SINGLETON_ID)
        session.add(stored_account)

    stored_account.broker_account_id = account.broker_account_id
    stored_account.masked_id = account.masked_id
    stored_account.display_name = account.display_name
    stored_account.currency = account.currency

    await session.flush()

    # Позиции заменяются целиком: инструменты могли исчезнуть из портфеля.
    await session.execute(
        delete(PortfolioPosition).where(PortfolioPosition.state_id == SINGLETON_ID)
    )

    state = await session.get(AccountState, SINGLETON_ID)
    if state is None:
        state = AccountState(id=SINGLETON_ID, account_id=SINGLETON_ID)
        session.add(state)

    state.account_id = SINGLETON_ID
    state.captured_at = snapshot.captured_at
    state.total_value = snapshot.total_value
    state.cash = snapshot.cash
    state.positions_cost_basis = snapshot.positions_cost_basis
    state.unrealized_pnl = snapshot.unrealized_pnl
    state.positions_count = snapshot.positions_count

    await session.flush()

    for order, position in enumerate(snapshot.positions):
        session.add(
            PortfolioPosition(
                state_id=SINGLETON_ID,
                instrument_uid=position.instrument_uid,
                ticker=position.ticker,
                name=position.name,
                asset_type=position.asset_type,
                currency=position.currency,
                quantity=position.quantity,
                average_price=position.average_price,
                current_price=position.current_price,
                accrued_interest=position.accrued_interest,
                value=position.value,
                unrealized_pnl=position.unrealized_pnl,
                sort_order=order,
            )
        )

    sync_state = await get_sync_state(session)
    sync_state.broker_status = "connected"
    sync_state.last_status = "ok"
    sync_state.last_attempt_at = snapshot.captured_at
    sync_state.last_success_at = snapshot.captured_at
    sync_state.failure_reason_code = None
    sync_state.failure_detail = None
    sync_state.consecutive_failures = 0

    await session.flush()


async def record_failure(
    session: AsyncSession,
    *,
    reason_code: str,
    detail: str,
    broker_status: str | None = None,
    attempted_at: dt.datetime | None = None,
) -> None:
    """Фиксирует неуспешную попытку, не трогая сохранённое состояние."""
    sync_state = await get_sync_state(session)
    sync_state.last_status = "failed"
    sync_state.last_attempt_at = attempted_at or dt.datetime.now(dt.UTC)
    sync_state.failure_reason_code = reason_code
    sync_state.failure_detail = detail or None
    sync_state.consecutive_failures = sync_state.consecutive_failures + 1
    if broker_status is not None:
        sync_state.broker_status = broker_status

    await session.flush()
