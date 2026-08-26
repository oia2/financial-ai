"""Состояние портфеля: чтение сохранённого состояния.

Backend-API не обращается к брокеру. Он отдаёт то, что сохранил Worker, плюс
статус подключения и статус синхронизации — чтобы frontend ничего не вычислял
сам (FR-015, FR-037).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.api.schemas import (
    AccountOut,
    BrokerOut,
    PortfolioOut,
    PositionOut,
    RefreshResultOut,
    SnapshotOut,
    SyncOut,
)
from financial_ai.config import get_settings
from financial_ai.db import repository
from financial_ai.db.engine import get_session
from financial_ai.db.models import AccountState, BrokerSyncState, PortfolioPosition
from financial_ai.db.settings_repo import get_interval_seconds
from financial_ai.domain.portfolio import age_seconds, is_stale, percent, share, stale_after_seconds
from financial_ai.sync import advisory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio"])


@router.get("/portfolio", response_model=PortfolioOut)
async def get_portfolio(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PortfolioOut:
    """Полное состояние раздела «Портфель» одним ответом."""
    # Возраст данных должен быть честным — ответ не кэшируется.
    response.headers["Cache-Control"] = "no-store"

    account = await repository.get_account(session)
    state = await repository.get_state(session)
    positions = await repository.get_positions(session) if state is not None else []
    sync_state = await repository.get_sync_state(session)
    interval = await get_interval_seconds(session)
    # Идёт ли синхронизация прямо сейчас — видно по advisory lock, который
    # держит Worker; отдельного канала между сервисами для этого не нужно.
    in_progress = await advisory.is_held(session)

    now = dt.datetime.now(dt.UTC)

    return PortfolioOut(
        broker=BrokerOut(
            status=sync_state.broker_status,
            account=AccountOut.model_validate(account) if account is not None else None,
        ),
        snapshot=_build_snapshot_out(state, positions, now),
        sync=_build_sync_out(sync_state, state, interval, now, in_progress),
    )


def _build_snapshot_out(
    state: AccountState | None,
    positions: list[PortfolioPosition],
    now: dt.datetime,
) -> SnapshotOut | None:
    if state is None:
        return None

    total = state.total_value

    return SnapshotOut(
        captured_at=state.captured_at,
        age_seconds=age_seconds(state.captured_at, now) or 0,
        total_value=total,
        cash=state.cash,
        # При нулевом итоге доля равна нулю, деления не происходит.
        cash_share=share(state.cash, total),
        unrealized_pnl=state.unrealized_pnl,
        # None, а не ложный ноль, когда база стоимости нулевая.
        unrealized_pnl_percent=percent(state.unrealized_pnl, state.positions_cost_basis),
        positions_count=state.positions_count,
        positions=[_build_position_out(p, total) for p in positions],
    )


def _build_position_out(position: PortfolioPosition, total: object) -> PositionOut:
    cost_basis = (
        None if position.average_price is None else position.quantity * position.average_price
    )

    return PositionOut(
        instrument_uid=position.instrument_uid,
        ticker=position.ticker,
        name=position.name,
        asset_type=position.asset_type,
        currency=position.currency,
        quantity=position.quantity,
        average_price=position.average_price,
        current_price=position.current_price,
        value=position.value,
        unrealized_pnl=position.unrealized_pnl,
        unrealized_pnl_percent=percent(position.unrealized_pnl, cost_basis),
        share=share(position.value, total),  # type: ignore[arg-type]
    )


def _build_sync_out(
    sync_state: BrokerSyncState,
    state: AccountState | None,
    interval_seconds: int,
    now: dt.datetime,
    in_progress: bool,
) -> SyncOut:
    captured_at = state.captured_at if state is not None else None

    return SyncOut(
        status=sync_state.last_status,
        last_success_at=sync_state.last_success_at,
        last_attempt_at=sync_state.last_attempt_at,
        failure_reason_code=sync_state.failure_reason_code,
        # Порог устаревания считает backend: frontend не должен повторять
        # эту логику (FR-040).
        is_stale=is_stale(captured_at, now, interval_seconds),
        stale_after_seconds=stale_after_seconds(interval_seconds),
        refresh_interval_seconds=interval_seconds,
        in_progress=in_progress,
    )


@router.post("/portfolio/refresh", response_model=RefreshResultOut)
async def refresh_portfolio() -> RefreshResultOut:
    """Ручное обновление (US3).

    Backend-API не ходит к брокеру: он транслирует команду Backend-Worker,
    у которого есть токен и общий лок с фоновым циклом.
    """
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=settings.worker_sync_timeout_seconds) as client:
            response = await client.post(f"{settings.worker_internal_url}/internal/sync")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as error:
        # Worker недоступен. Для интерфейса это ошибка синхронизации, а не
        # обрыв связи с сервером: ответ получен, значит сервер жив.
        logger.warning("Backend-Worker недоступен для ручного обновления")
        raise HTTPException(status_code=503, detail={"code": "worker_unavailable"}) from error

    return RefreshResultOut(
        status=payload["status"],
        deduplicated=payload["deduplicated"],
        captured_at=payload["captured_at"],
        failure_reason_code=payload["failure_reason_code"],
    )
