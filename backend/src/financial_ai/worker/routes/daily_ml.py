"""Внутреннее управление жизненным циклом Daily ML.

Внутренний интерфейс, наружу не выставляется: у контейнера worker нет проброса
портов. Публичная граница — `/api/daily-ml/*` в Backend-API, которая передаёт
команды сюда.

Здесь живут только команды, требующие доступа к планировщику: он владеет
паузой и очередью, и они существуют в процессе. Чтение истории идёт из
хранилища и проходит мимо этого интерфейса.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from financial_ai.config import Settings, get_settings
from financial_ai.daily_ml import readiness, runner
from financial_ai.daily_ml import reconcile as reconcile_module
from financial_ai.daily_ml.repository import DailyMlRepository
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import groups
from financial_ai.market_data.calendar import moscow_now
from financial_ai.market_data.repository import MarketDataRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["daily-ml"])


class PauseRequest(BaseModel):
    """Включение и выключение автоматического режима."""

    paused: bool


def _scheduler(request: Request) -> Any:
    return getattr(request.app.state, "daily_ml_scheduler", None)


# Фоновые обработки очереди. Ссылки держатся, иначе задача может быть собрана
# сборщиком мусора до завершения.
_kicks: set[asyncio.Task[None]] = set()


def _kick(settings: Settings) -> None:
    """Подтолкнуть обработку очереди, не дожидаясь её окончания.

    Ответ команды **не должен ждать инференса**. У эмулятора он занимает
    секунды, у настоящей модели — минуты, и синхронная обработка упиралась бы в
    таймаут HTTP: команда выполнялась, а вызывающий получал «сборщик
    недоступен». Это наблюдалось на стенде.

    Одновременность безопасна: обработчик берёт advisory-блокировку, и второй
    вызов просто уходит ни с чем.
    """

    async def run() -> None:
        factory = get_session_factory()
        try:
            async with factory() as session:
                await runner.process_queue(session, settings)
        except Exception:
            logger.exception("фоновая обработка очереди ранжирования не выполнена")

    task = asyncio.create_task(run(), name="daily-ml-kick")
    _kicks.add(task)
    task.add_done_callback(_kicks.discard)


@router.post("/daily-ml/reconcile")
async def reconcile_now(request: Request) -> dict[str, object]:
    """Немедленная проверка. НЕ принудительный пересчёт.

    Уже успешно обработанный вход пропускается: «проверить сейчас» означает
    «посмотри, есть ли работа», а не «посчитай заново».
    """
    scheduler = _scheduler(request)
    paused = bool(scheduler.paused) if scheduler is not None else False

    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        result = await reconcile_module.reconcile(session, settings, paused)

    # Задания созданы — этого достаточно для ответа. Их выполнение идёт фоном:
    # ждать инференса в HTTP-запросе нельзя.
    if not paused:
        _kick(settings)

    return {
        "queued": result.queued,
        "already_up_to_date": result.already_up_to_date,
        "latest_data_ready": result.latest_data_ready.isoformat()
        if result.latest_data_ready
        else None,
        "latest_ml_success": result.latest_ml_success.isoformat()
        if result.latest_ml_success
        else None,
        "data_gap_sessions": result.data_gap_sessions,
        "paused": result.paused,
        "notes": result.notes,
    }


@router.put("/daily-ml/settings")
async def set_paused(payload: PauseRequest, request: Request) -> dict[str, object]:
    """Пауза автоматического режима.

    Останавливает создание заданий, а не выполнение: начатый прогон доводится
    до конца. Сбор рыночных данных пауза не останавливает — это разные
    механизмы.
    """
    scheduler = _scheduler(request)
    if scheduler is not None:
        scheduler.set_paused(payload.paused)

    return {"paused": payload.paused}


@router.get("/daily-ml/settings")
async def read_settings(request: Request) -> dict[str, object]:
    scheduler = _scheduler(request)
    return {"paused": bool(scheduler.paused) if scheduler is not None else False}


@router.get("/daily-ml/state")
async def read_state(request: Request) -> dict[str, object]:
    """Готовность данных и разрыв — глазами сборщика.

    Считается здесь, а не в `backend-api`, по тому же правилу, что и сводка
    полноты: перечень обязательных групп и глубины окон задаются конфигурацией
    **worker'а**, и второй счёт по другой конфигурации разойдётся с первым.
    Расхождение уже наблюдалось на стенде: раздел показывал «готовых данных
    нет» рядом с успешным прогоном ровно за эту дату.

    Маршрут только читает: заданий он не создаёт и очередь не трогает.
    """
    settings = get_settings()
    scheduler = _scheduler(request)

    factory = get_session_factory()
    async with factory() as session:
        last_known = await MarketDataRepository(session).latest_trading_session(moscow_now().date())
        ready = (
            await readiness.latest_ready(session, settings, last_known)
            if last_known is not None
            else None
        )
        gap = await reconcile_module.data_gap(session)

        # Чего именно не хватает — говорится, а не подразумевается: «ожидаются
        # данные» без ответа «каких?» оставляет человека гадать.
        #
        # Считается не только когда готовой даты нет вовсе, но
        # и когда она ОТСТАЛА от последней сессии календаря. Прежнее условие
        # молчало во втором случае: раздел показывал «данные готовы по 04.09»
        # рядом с пустым списком причин, хотя 11.09 был заблокирован. Человек
        # видел, что расчёт стоит, и не видел почему.
        blocking: list[dict[str, str]] = []
        if last_known is not None and ready != last_known:
            missing = (await readiness.evaluate(session, settings, last_known)).missing_groups
            # Название берётся из реестра групп — оттуда же, откуда его берёт
            # сводка рыночных данных. Второго объявления одного факта не
            # заводится: два однажды разойдутся.
            titles = {group.group_id.value: group.title for group in groups.GROUPS}
            blocking = [{"group": name, "title": titles.get(name, name)} for name in missing]

    # Устаревание берётся из последней реконсиляции, а не считается здесь:
    # ответ на этот вопрос стоит пересборки набора — секунды процессорного
    # времени, — и на маршруте, который опрашивают, ему не место.
    last = scheduler.last_result if scheduler is not None else None

    return {
        "paused": bool(scheduler.paused) if scheduler is not None else False,
        "latest_data_ready": ready.isoformat() if ready else None,
        "data_gap_sessions": gap,
        "blocking_groups": blocking,
        "stale_latest": last.stale_latest if last is not None else None,
    }


@router.post("/daily-ml/runs/{run_id}/retry")
async def retry_run(run_id: int, request: Request) -> dict[str, object]:
    """Повторить отказавший прогон.

    Повтор недоступен, если входной набор удалён по сроку хранения: повторять
    нечем, и кнопка, которая всё равно не сработает, хуже её отсутствия.
    """
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        repository = DailyMlRepository(session)
        run = await repository.get(run_id)

        if run is None:
            return {"error": "run_not_found"}
        if run.status != "failed":
            return {"error": "run_not_failed"}
        if run.attempt >= settings.daily_ml_max_attempts:
            return {"error": "attempts_exhausted"}

        from pathlib import Path
        from urllib.parse import urlparse

        dataset_path = Path(urlparse(run.dataset_ref).path.lstrip("/"))
        if not dataset_path.exists() and not Path(urlparse(run.dataset_ref).path).exists():
            return {"error": "dataset_expired"}

        await repository.requeue(run_id)
        await session.commit()

    # Ответ говорит о том, что уже произошло: прогон снова в очереди. Исход
    # повтора станет известен позже и увидится в истории.
    _kick(settings)
    return {"status": "queued", "attempt": run.attempt + 1}
