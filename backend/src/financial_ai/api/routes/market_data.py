"""Публичная граница раздела «Рыночные данные» (фича 006).

Четыре операции: чтение сводки полноты, чтение состояния прогона, запуск догона
и мягкая остановка. Все четыре передаются внутреннему интерфейсу Backend-Worker
— тем же способом, каким `POST /api/portfolio/refresh` передаёт синхронизацию.

**Почему передаёт, а не считает сам.** Полнота считается относительно окна
сбора, а окно задаётся конфигурацией worker (`MARKET_DATA_*_WINDOW_SESSIONS`).
У настроек есть умолчания, поэтому Backend-API, считая сводку сам, не упал бы,
а молча посчитал бы по другому окну, стоило оператору изменить окно у worker.
Владелец окна должен остаться один.

**Ответ передаётся как есть.** Форма ответа задана контрактом фичи 005, и
повторное описание её моделями здесь дало бы второй источник истины. Хуже того,
в сводке значима разница между «поля нет» и «поле равно null»: у группы без
истории полей окна нет вовсе, а ноль читался бы как «ничего не собрано»
(FR-014, FR-015). Модель, собранная по умолчаниям, эту разницу стирает.

Наружу этот модуль выносит только `/api/market-data/*`. Внутренний интерфейс
worker остаётся недоступным за пределами сети развёртывания (FR-046): у его
контейнера нет проброса портов, и nginx его не проксирует.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from financial_ai.api.schemas import CatchupStartIn
from financial_ai.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-data"])


async def _worker(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Response:
    """Обращение к внутреннему интерфейсу worker.

    Отказ операции и недоступность сборщика — разные вещи, и различать их
    обязан именно этот слой (FR-042). Ответ worker'а, каким бы ни был его код,
    передаётся дальше как есть: worker уже описал исход телом. `503` возникает
    только тогда, когда ответа не было вовсе.
    """
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=settings.worker_sync_timeout_seconds) as client:
            response = await client.request(
                method,
                f"{settings.worker_internal_url}{path}",
                params=params,
                json=payload,
            )
    except httpx.HTTPError as error:
        logger.warning("Backend-Worker недоступен: %s %s", method, path)
        raise HTTPException(
            status_code=503,
            detail={"code": "worker_unavailable", "message": "сборщик данных недоступен"},
        ) from error

    # Возраст данных должен быть честным — ответ не кэшируется.
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/market-data/coverage")
async def read_coverage(
    asof: Annotated[dt.date | None, Query(description="Дата решения")] = None,
) -> Response:
    """Сводка полноты по группам источников.

    Отчёт о полноте, а не просмотр данных: конкретных значений наблюдений в
    ответе нет (FR-008).
    """
    params = {"asof": asof.isoformat()} if asof else None
    return await _worker("GET", "/internal/coverage", params=params)


@router.get("/market-data/catchup")
async def read_catchup() -> Response:
    """Состояние прогона.

    После перезапуска сборщика приходит `idle`: состояние живёт в процессе и
    вместе с ним исчезает. Интерфейс на этом основании отменяет ранее
    показанный идущий прогон (FR-035).
    """
    return await _worker("GET", "/internal/catchup")


@router.post("/market-data/catchup")
async def start_catchup(payload: CatchupStartIn) -> Response:
    """Запустить догон.

    Проверка диапазона остаётся на стороне worker: он же владеет окном и
    планом, и вторая проверка здесь разошлась бы с первой.
    """
    return await _worker(
        "POST",
        "/internal/catchup",
        payload=payload.model_dump(mode="json", exclude_none=True),
    )


@router.delete("/market-data/catchup")
async def stop_catchup() -> Response:
    """Остановить догон.

    Остановка мягкая: текущая сессия доводится до конца. Ответ означает, что
    остановка запрошена, а не что она состоялась — в остановленное состояние
    интерфейс переходит по состоянию прогона (FR-032).
    """
    return await _worker("DELETE", "/internal/catchup")
