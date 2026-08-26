"""Внутренний REST синхронизации.

Наружу через nginx не проксируется: единственный клиент — Backend-API.
Ручное и фоновое обновление используют одну и ту же функцию (FR-006), поэтому
одновременное выполнение исключено общим локом (FR-029).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sync"])


@router.post("/sync")
async def sync(request: Request) -> dict[str, Any]:
    """Запускает синхронизацию или присоединяется к уже идущей.

    Неуспех брокера — это 200 с ``status: failed``, а не 5xx: обращение к
    worker'у состоялось, и ответ описывает его исход. 5xx означал бы отказ
    самого worker'а.
    """
    scheduler = request.app.state.scheduler
    result, deduplicated = await scheduler.run_once()

    was_deduplicated = deduplicated or result.deduplicated

    logger.info(
        "Ручная синхронизация завершена",
        extra={"ctx_status": result.status, "ctx_deduplicated": was_deduplicated},
    )

    return {
        "status": result.status,
        "deduplicated": was_deduplicated,
        "captured_at": result.captured_at.isoformat() if result.captured_at else None,
        "failure_reason_code": result.failure_reason_code,
        "duration_ms": result.duration_ms,
    }
