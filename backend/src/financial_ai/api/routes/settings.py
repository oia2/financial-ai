"""Настройка интервала автообновления (FR-031, FR-035).

Границы диапазона приходят вместе со значением, чтобы интерфейс показывал
допустимый диапазон, а не хардкодил его.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.api.schemas import RefreshIntervalIn, RefreshIntervalOut
from financial_ai.db.engine import get_session
from financial_ai.db.models import INTERVAL_MAX_SECONDS, INTERVAL_MIN_SECONDS
from financial_ai.db.settings_repo import (
    IntervalOutOfRangeError,
    get_interval,
    set_interval_seconds,
)

router = APIRouter(tags=["settings"])


@router.get("/settings/refresh-interval", response_model=RefreshIntervalOut)
async def read_interval(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RefreshIntervalOut:
    interval = await get_interval(session)
    return RefreshIntervalOut(
        interval_seconds=interval.interval_seconds,
        min_seconds=interval.min_seconds,
        max_seconds=interval.max_seconds,
        default_seconds=interval.default_seconds,
    )


@router.put("/settings/refresh-interval", response_model=RefreshIntervalOut)
async def update_interval(
    payload: RefreshIntervalIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RefreshIntervalOut:
    """Сохраняет новый интервал.

    Недопустимое значение отклоняется, прежний интервал продолжает
    действовать (US2 AS4). Новое значение применяется к следующему циклу
    фоновой синхронизации без перезапуска (SC-012).
    """
    try:
        interval = await set_interval_seconds(session, payload.interval_seconds)
    except IntervalOutOfRangeError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "interval_out_of_range",
                "min_seconds": INTERVAL_MIN_SECONDS,
                "max_seconds": INTERVAL_MAX_SECONDS,
            },
        ) from error

    await session.commit()
    return RefreshIntervalOut(
        interval_seconds=interval.interval_seconds,
        min_seconds=interval.min_seconds,
        max_seconds=interval.max_seconds,
        default_seconds=interval.default_seconds,
    )
