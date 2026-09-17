"""Публичная граница раздела «Ранжирование» (фича 007).

Чтение — из хранилища: история прогонов и их результаты лежат в БД, ходить за
ними к worker незачем. Команды — передачей внутреннему интерфейсу worker: пауза
и очередь живут в его процессе.

Разделение не косметическое. История переживает удаление набора и перезапуск
сборщика; состояние планировщика — нет. Читать первое через второе значило бы
поставить показ истории в зависимость от того, жив ли сборщик.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import get_settings
from financial_ai.daily_ml import readiness
from financial_ai.daily_ml.models import RunStatus
from financial_ai.daily_ml.repository import DailyMlRepository
from financial_ai.db.engine import get_session
from financial_ai.market_data.calendar import moscow_now

logger = logging.getLogger(__name__)

router = APIRouter(tags=["daily-ml"])


async def _worker(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """Передать команду внутреннему интерфейсу worker."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=settings.worker_sync_timeout_seconds) as client:
            response = await client.request(
                method, f"{settings.worker_internal_url}{path}", json=payload
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as error:
        logger.warning("Backend-Worker недоступен: %s %s", method, path)
        raise HTTPException(
            status_code=503,
            detail={"code": "worker_unavailable", "message": "сборщик данных недоступен"},
        ) from error


def _next_check_at(now: dt.datetime, after_close: str) -> dt.datetime:
    """Когда планировщик посмотрит в следующий раз.

    Это время **проверки**, а не начала расчёта: начало зависит от готовности
    данных, которая заранее неизвестна. Поля для него в ответе нет намеренно.
    """
    try:
        hours, minutes = after_close.split(":", 1)
        target = dt.time(int(hours), int(minutes))
    except (ValueError, IndexError):
        target = dt.time(19, 30)

    # Часовой пояс берётся у переданного момента: `moscow_now` возвращает
    # осведомлённое время, и наивная склейка сравнивалась бы с ним неверно.
    today = dt.datetime.combine(now.date(), target, tzinfo=now.tzinfo)
    return today if now < today else today + dt.timedelta(days=1)


def _run_row(run: Any) -> dict[str, Any]:
    duration = None
    if run.started_at and run.finished_at:
        duration = int((run.finished_at - run.started_at).total_seconds())

    return {
        "id": run.id,
        "asof_date": run.asof_date.isoformat(),
        "status": run.status,
        "model_id": run.model_id,
        "model_version": run.model_version,
        "attempt": run.attempt,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_seconds": duration,
        "included_asset_count": run.included_asset_count,
        "error_code": run.error_code,
        "error_message": run.error_message,
        # Звено само объявляет себя эмулятором, и признак хранится вместе с
        # прогоном: по имени модели о нём догадываться нельзя.
        "emulated": run.emulated,
    }


@router.get("/daily-ml/status")
async def read_status(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Состояние раздела одним ответом."""
    response.headers["Cache-Control"] = "no-store"

    settings = get_settings()
    repository = DailyMlRepository(session)

    latest_success = await repository.latest_success()
    running = await repository.running_run()
    queued = await repository.queued_runs()
    last_failure = await repository.last_failure()
    queue_completed, queue_total = await repository.queue_progress()

    # Готовность данных и разрыв считает **сборщик**: перечень обязательных
    # групп и глубины окон задаются его конфигурацией, и второй счёт по другой
    # конфигурации разойдётся с первым. Расхождение уже наблюдалось на стенде:
    # раздел показывал «готовых данных нет» рядом с успешным прогоном ровно за
    # эту дату.
    paused = False
    market_last: dt.date | None = None
    gap = 0
    readiness_known = False
    stale_latest: bool | None = None
    blocking: list[dict[str, str]] = []

    try:
        state = await _worker("GET", "/internal/daily-ml/state")
        paused = bool(state.get("paused"))
        raw_ready = state.get("latest_data_ready")
        market_last = dt.date.fromisoformat(raw_ready) if isinstance(raw_ready, str) else None
        gap = int(state.get("data_gap_sessions") or 0)
        # Устаревание входа приходит от сборщика: он уже собрал набор ради
        # дайджеста на своём тике. Считать его здесь значило бы платить секунды
        # процессорного времени на каждый опрос — и класть этим весь процесс.
        raw_stale = state.get("stale_latest")
        stale_latest = raw_stale if isinstance(raw_stale, bool) else None
        raw_blocking = state.get("blocking_groups")
        blocking = (
            [
                {"group": str(item.get("group")), "title": str(item.get("title"))}
                for item in raw_blocking
                if isinstance(item, dict)
            ]
            if isinstance(raw_blocking, list)
            else []
        )
        readiness_known = True
    except (HTTPException, ValueError):
        # Сборщик не ответил. Это не отказ чтения: история уже прочитана из
        # хранилища, и показать её честнее, чем ничего. Но «готовой даты нет» и
        # «готовность неизвестна» — разные утверждения, и второе не выдаётся за
        # первое: признак уходит в ответ.
        logger.info("готовность данных неизвестна: сборщик не ответил")

    status = _status_of(
        paused=paused,
        running=running is not None,
        queued=len([run for run in queued if run.status == RunStatus.QUEUED.value]),
        gap=gap,
        data_ready=market_last,
        ml_success=latest_success.asof_date if latest_success else None,
        failed=last_failure is not None
        and (latest_success is None or last_failure.asof_date >= latest_success.asof_date),
    )

    return {
        "status": status,
        "paused": paused,
        "latest_data_ready": market_last.isoformat() if market_last else None,
        "latest_ml_success": latest_success.asof_date.isoformat() if latest_success else None,
        "current": {
            "asof_date": running.asof_date.isoformat(),
            "started_at": running.started_at.isoformat() if running.started_at else None,
            "attempt": running.attempt,
        }
        if running
        else None,
        "queue": [{"asof_date": run.asof_date.isoformat(), "status": run.status} for run in queued],
        # Счётный прогресс очереди. Доли готовности внутри одной даты нет: её
        # никто не сообщает, и полоса по таймеру браузера была бы выдумкой.
        "queue_progress": {"completed": queue_completed, "total": queue_total},
        "last_error": last_failure.error_message if last_failure else None,
        "next_check_at": _next_check_at(
            moscow_now(), settings.market_data_ingest_after_close
        ).isoformat(),
        "data_gap_sessions": gap,
        # Готовность данных известна, только если сборщик ответил. «Нет готовой
        # даты» и «неизвестно» — разные утверждения.
        "readiness_known": readiness_known,
        # Чего не хватает — говорится, а не подразумевается: «ожидаются данные»
        # без ответа «каких?» оставляет человека гадать.
        "blocking_groups": blocking,
        "stale_latest": stale_latest,
        "model_id": latest_success.model_id if latest_success else None,
        "model_version": latest_success.model_version if latest_success else None,
        "emulated": latest_success.emulated if latest_success else None,
    }


def _status_of(
    *,
    paused: bool,
    running: bool,
    queued: int,
    gap: int,
    data_ready: dt.date | None,
    ml_success: dt.date | None,
    failed: bool,
) -> str:
    """Одно слово о состоянии раздела.

    Порядок проверок — порядок важности для человека: сначала то, что требует
    его вмешательства.
    """
    if running:
        return "running"
    if gap > 0:
        return "data_gap"
    if paused:
        return "paused"
    if failed:
        return "failed"
    if queued:
        return "lagging"
    if data_ready is not None and data_ready == ml_success:
        return "up_to_date"
    return "waiting"


@router.get("/daily-ml/runs")
async def read_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query(description="Отбор по состоянию")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """История прогонов, новые сверху.

    Признак устаревания здесь НЕ считается: он требует пересборки набора и
    нужен только при открытии прогона.
    """
    runs, total = await DailyMlRepository(session).history(status, limit, offset)
    return {"total": total, "items": [_run_row(run) for run in runs]}


@router.get("/daily-ml/runs/{run_id}")
async def read_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Один прогон: вход, исход, результат.

    Признак устаревания **не вычисляется здесь**. Ответ на него стоит пересборки
    набора — секунды процессорного времени на 314 сессий и 512 активов, — и на
    пути, которого ждёт человек, ему не место: работа упирается в процессор и
    держит весь процесс. Ответ берётся у сборщика, который уже собрал набор на
    своём тике, и известен он для последней готовой даты. Для остальных дат
    возвращается `null` — «не рассчитан», а не «не устарел».
    """
    repository = DailyMlRepository(session)

    run = await repository.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": "прогон не найден"},
        )

    items = await repository.items(run_id, limit, offset)
    available = _dataset_available(run.dataset_ref)

    stale: bool | None = None
    if run.status == RunStatus.SUCCESS.value and run.finished_at is not None:
        # Дешёвый вопрос: собирали ли что-нибудь после прогона. Если нет — вход
        # измениться не мог, и ответ получен без пересборки набора.
        stale = await readiness.is_stale(
            session,
            get_settings(),
            run.asof_date,
            run.dataset_digest,
            since=run.finished_at,
            window=(run.window_from, run.window_till),
        )

    row = _run_row(run)
    row["input"] = {
        "dataset_digest": run.dataset_digest,
        "dataset_ref": run.dataset_ref,
        "dataset_available": available,
        # Окно и полнота читаются из прогона, а не считаются сейчас: глубина
        # окна — настройка, а полнота меняется с приходом данных. Пересчёт дал
        # бы сегодняшний ответ на вчерашний вопрос.
        "window_from": run.window_from.isoformat() if run.window_from else None,
        "window_till": run.window_till.isoformat() if run.window_till else None,
        "complete": run.input_complete,
    }
    row["stale"] = stale
    row["items"] = [
        {
            "rank": item.rank,
            "asset_id": item.asset_id,
            "price_series_id": item.price_series_id,
            # Скор строкой: он участвует в сортировке, и float исказил бы
            # порядок у потребителя.
            "score": format(item.score, "f"),
        }
        for item in items
    ]
    return row


def _dataset_available(ref: str) -> bool:
    """Существует ли ещё входной набор.

    Наборы удаляются ретеншеном через 30 дней. Результат в истории остаётся, а
    повтор по нему становится невозможен — и это надо показать, а не скрыть.
    """
    from pathlib import Path
    from urllib.parse import urlparse

    path = urlparse(ref).path
    return Path(path).exists() or Path(path.lstrip("/")).exists()


@router.post("/daily-ml/reconcile")
async def reconcile_now() -> dict[str, Any]:
    """Немедленная проверка. Уже посчитанное пропускается."""
    return await _worker("POST", "/internal/daily-ml/reconcile")


@router.put("/daily-ml/settings")
async def set_paused(payload: dict[str, bool]) -> dict[str, Any]:
    """Пауза автоматического режима. Сбор данных она не останавливает."""
    return await _worker(
        "PUT", "/internal/daily-ml/settings", {"paused": bool(payload.get("paused"))}
    )


@router.post("/daily-ml/runs/{run_id}/retry")
async def retry_run(run_id: int) -> dict[str, Any]:
    """Повторить отказавший прогон."""
    result = await _worker("POST", f"/internal/daily-ml/runs/{run_id}/retry")

    error = result.get("error") if isinstance(result, dict) else None
    if error:
        messages = {
            "run_not_found": (404, "прогон не найден"),
            "run_not_failed": (409, "повторять можно только отказавший прогон"),
            "dataset_expired": (409, "входной набор удалён по сроку хранения"),
            "attempts_exhausted": (409, "попытки исчерпаны"),
        }
        status_code, message = messages.get(str(error), (409, "повтор невозможен"))
        raise HTTPException(status_code=status_code, detail={"code": error, "message": message})

    return result
