"""Health-эндпоинт Backend-Worker."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Живость worker'а, доступность БД и состояние планировщика.

    Поле ``broker_token`` сообщает только ФАКТ наличия токена и никогда его
    значение (FR-023, SC-009).
    """
    settings = get_settings()
    scheduler = getattr(request.app.state, "scheduler", None)

    payload: dict[str, Any] = {
        "status": "ok",
        "database": "ok",
        "scheduler": "running" if scheduler is not None and scheduler.is_running else "stopped",
        "broker_token": "configured" if settings.broker_token_configured else "missing",
        "current_interval_seconds": getattr(scheduler, "current_interval_seconds", None),
    }

    try:
        await session.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        payload["status"] = "degraded"
        payload["database"] = "unavailable"
        return payload

    if payload["scheduler"] != "running":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        payload["status"] = "degraded"

    return payload
