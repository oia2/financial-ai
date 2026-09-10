"""Сводка состояния данных по группам источников.

Внутренний интерфейс, как и управление догоном. Отчёт о полноте: конкретных
значений наблюдений он не выдаёт.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import coverage as coverage_module
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.repository import MarketDataRepository

router = APIRouter(tags=["coverage"])


@router.get("/coverage")
async def get_coverage(
    asof: Annotated[dt.date | None, Query(description="Дата решения")] = None,
) -> object:
    """Состояние данных по группам на дату решения."""
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        resolved = asof
        if resolved is None:
            calendar = TradingCalendar(MarketDataRepository(session))
            resolved = await calendar.latest_session(moscow_today())

        if resolved is None:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "calendar_empty",
                        "message": "календарь пуст: сначала выполните сбор",
                    }
                },
            )

        return await coverage_module.build_report(session, settings, resolved)
