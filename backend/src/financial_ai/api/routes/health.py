"""Health-эндпоинт Backend-API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.db.engine import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Живость сервиса и доступность БД.

    Используется Docker healthcheck. Интерфейс его не опрашивает: отсутствие
    связи наблюдается по основному запросу.
    """
    try:
        await session.execute(text("select 1"))
    except Exception:  # noqa: BLE001 — причина не раскрывается наружу
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unavailable"}

    return {"status": "ok", "database": "ok"}
