"""Сводка состояния данных по группам источников.

Внутренний интерфейс, как и управление догоном. Отчёт о полноте: конкретных
значений наблюдений он не выдаёт.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import coverage as coverage_module
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.scheduler import MarketDataScheduler

router = APIRouter(tags=["coverage"])


class CollectionPauseIn(BaseModel):
    """Остановка и возобновление автоматического сбора."""

    paused: bool


def _scheduler(request: Request) -> MarketDataScheduler | None:
    scheduler = getattr(request.app.state, "market_data_scheduler", None)
    return scheduler if isinstance(scheduler, MarketDataScheduler) else None


@router.get("/market-data/settings")
async def read_collection_settings(request: Request) -> dict[str, bool]:
    """Идёт ли автоматический сбор."""
    scheduler = _scheduler(request)
    return {"paused": scheduler.paused if scheduler is not None else False}


@router.put("/market-data/settings")
async def set_collection_paused(payload: CollectionPauseIn, request: Request) -> dict[str, bool]:
    """Остановить или возобновить автоматический сбор.

    Останавливается создание НОВОЙ работы: начатая сессия доводится до конца.
    Ранжирование этим переключателем не управляется — у него своя пауза, и это
    разные механизмы (FR-029e). Управляемый догон тоже продолжает работать: он
    запускается явной командой человека (FR-029g).
    """
    scheduler = _scheduler(request)
    if scheduler is not None:
        scheduler.set_paused(payload.paused)
    return {"paused": payload.paused}


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


@router.get("/calendar")
async def calendar_month(month: str | None = None) -> dict[str, object]:
    """Состояние дней месяца: факт слева от сегодня, ничего справа.

    Календарь строится по состоявшимся торгам, поэтому будущих дней сервер не
    утверждает вовсе — их помечает ожиданием интерфейс (spec 008, FR-023).
    """
    from financial_ai.market_data import calendar_view

    today = moscow_today()
    year, month_number = today.year, today.month
    if month:
        try:
            year, month_number = (int(part) for part in month.split("-", 1))
        except ValueError:
            year, month_number = today.year, today.month

    factory = get_session_factory()
    async with factory() as session:
        return await calendar_view.build_month(session, get_settings(), year, month_number)
