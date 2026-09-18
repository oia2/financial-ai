"""Управление догоном пропущенных сессий.

Внутренний интерфейс, наружу не выставляется: у контейнера worker'а нет проброса
портов, он доступен только внутри сети развёртывания. Этот интерфейс управляет
обращениями к бирже и публичным быть не должен.

Образец ответов взят у соседнего `/internal/sync`: обращение состоялось — значит
`200`, а исход описан телом; `5xx` означал бы отказ самого worker'а. Отличие
одно: повторный запуск при идущем догоне **отклоняется**, а не присоединяется к
идущему. Синхронизация счёта идемпотентна и коротка, а два одновременных догона
писали бы одни и те же строки и удваивали обращения к бирже.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from financial_ai.market_data.groups import UnknownGroupError
from financial_ai.market_data.runner import (
    BackfillRequiredError,
    CatchupAlreadyRunningError,
    CatchupStatus,
    NothingToCatchUpError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catchup"])


class CatchupRequest(BaseModel):
    """Что и за какой период догонять. Всё необязательно."""

    groups: list[str] | None = Field(
        default=None,
        description="Группы источников. Пусто — все",
    )
    date_from: dt.date | None = Field(default=None, description="Начало диапазона")
    date_till: dt.date | None = Field(default=None, description="Конец диапазона")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """Единая форма ошибки, зафиксированная контрактом первой фичи."""
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )


@router.post("/catchup")
async def start_catchup(payload: CatchupRequest, request: Request) -> Any:
    """Запустить догон."""
    runner = request.app.state.catchup_runner

    if payload.date_from and payload.date_till and payload.date_from > payload.date_till:
        return _error(422, "invalid_range", "date_from позже date_till")

    try:
        state = await runner.start(payload.groups, payload.date_from, payload.date_till)
    except CatchupAlreadyRunningError as error:
        return _error(409, "catchup_already_running", str(error))
    except UnknownGroupError as error:
        return _error(422, "unknown_group", str(error))
    except BackfillRequiredError as error:
        # Не ошибка ввода, а состояние системы: на пустом хранилище нужна
        # первичная загрузка, а не догон.
        return _error(422, "backfill_required", str(error))
    except NothingToCatchUpError as error:
        return {"status": "idle", "requested_sessions": 0, "reason": str(error)}

    logger.info("догон запущен: сессий %s", state["requested"])
    return {
        "status": state["status"],
        "groups": state["groups"],
        "date_from": state["date_from"],
        "date_till": state["date_till"],
        "clamped": state["clamped"],
        "requested_sessions": state["requested"],
    }


@router.get("/catchup")
async def catchup_status(request: Request) -> dict[str, object]:
    """Ход работы — и по кнопке, и автоматического сбора.

    Оба показываются одним полем, потому что для человека это одна и та же
    работа: система идёт на биржу за сессиями. Разделять их на экране значило бы
    объяснять устройство там, где спрашивают о происходящем — и автоматический
    сбор оставался бы невидимым, хотя длится он ровно столько же.

    Приоритет у запуска по кнопке: это явная команда человека, и её ход он ждёт
    в первую очередь. Одновременность безопасна — сессия собирается под
    advisory-блокировкой.

    После перезапуска компонента возвращается ``idle``: состояние живёт в
    процессе и вместе с ним исчезает. Зависшего «идёт» не бывает по устройству.
    """
    runner = request.app.state.catchup_runner
    scheduler = getattr(request.app.state, "market_data_scheduler", None)
    auto = getattr(scheduler, "state", None)

    # Идущий прогон важнее любого прошедшего: их не бывает двух сразу.
    if runner.is_active:
        return runner.status()
    if auto is not None and auto.status in (CatchupStatus.RUNNING, CatchupStatus.STOPPING):
        return auto.snapshot()

    # Иначе показывается ПОСЛЕДНИЙ по времени начала — чей бы он ни был.
    # Прежде здесь стоял автоматический «только если идёт», и его итог исчезал
    # в тот же миг, как прогон кончился: человек, остановивший автосбор, видел
    # пустую панель и предложение начать ручной, будто ничего и не было
    # (FR-025).
    manual = runner.status()
    if auto is None or auto.started_at is None:
        return manual

    started = manual.get("started_at")
    if started is None or auto.started_at.isoformat() > str(started):
        return auto.snapshot()
    return manual


@router.get("/runs")
async def recent_runs(request: Request, limit: int = 5) -> dict[str, object]:
    """Журнал последних прогонов и изменений состава инструментов.

    Читается из таблицы исходов сбора, поэтому переживает перезапуск сборщика —
    в отличие от хода работы, который живёт в памяти процесса намеренно. Именно
    поэтому вопрос «как прошёл сбор» после перезапуска раньше оставался без
    ответа (spec 008, FR-005).

    Рядом идут изменения связей: появление фьючерса, смена семейства контрактов
    и исчезновение инструмента. Без них рост или убыль числа собранных бумаг
    выглядели бы пропуском сбора (FR-016).

    И причины пропусков: ход прогона живёт в памяти и исчезает с перезапуском,
    а причина обязана жить дольше — иначе человек видит дыру и не знает, ждать
    ему или вмешиваться (FR-002).
    """
    from financial_ai.db.engine import get_session_factory
    from financial_ai.market_data import journal

    factory = get_session_factory()
    async with factory() as session:
        runs = await journal.recent_runs(session, limit=limit)
        events = await journal.recent_link_events(session)
        skips = await journal.recent_skips(session)
        skips_total = await journal.count_skips(session)
        events_total = await journal.count_link_events(session)

    return {
        "runs": [run.to_dict() for run in runs],
        "events": [event.to_dict() for event in events],
        "events_total": events_total,
        "skips": [skip.to_dict() for skip in skips],
        "skips_total": skips_total,
    }


@router.delete("/catchup")
async def stop_catchup(request: Request) -> dict[str, object]:
    """Остановить работающий сбор — запущенный по кнопке или автоматический.

    Адресуется тому же прогону, который показан в состоянии: человек
    останавливает то, что видит, а не тот из двух механизмов, о котором он
    знать не обязан.

    Остановка мягкая: текущая сессия доводится до конца, дальнейшие не
    начинаются. День, собранный наполовину, неотличим от собранного полностью.

    Автоматический сбор при этом останавливается как ОДИН прогон: на следующем
    тике он начнётся снова. Чтобы он не начинался, есть пауза автосбора — это
    разные вещи, и подменять одну другой нельзя.
    """
    runner = request.app.state.catchup_runner
    if runner.is_active:
        return _stop_result(runner.stop())

    scheduler = getattr(request.app.state, "market_data_scheduler", None)
    if scheduler is not None and scheduler.state.status is CatchupStatus.RUNNING:
        return _stop_result(scheduler.request_stop().snapshot())

    return _stop_result(runner.stop())


def _stop_result(state: dict[str, object]) -> dict[str, object]:
    """Ответ об остановке: статус и сессия, которую доводим до конца."""
    current = state.get("current")
    session_date = current.get("session_date") if isinstance(current, dict) else None
    return {"status": state["status"], "current": session_date}
