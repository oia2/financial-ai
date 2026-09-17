"""Выполнение прогона Daily ML.

Один обработчик на процесс, задания берутся по одному от старой даты к новой.
Одновременность исключается advisory-блокировкой PostgreSQL: она переживает
несколько реплик worker, а блокировка внутри процесса — нет.

Порядок внутри прогона обязателен и неслучаен:

1. окончательная проверка полноты входа — по собранному набору, а не по
   дешёвой оценке: именно набор уходит модели;
2. запрос ранжирования;
3. запись результата и перевод в успех **одной транзакцией** — «успешно, но без
   строк» это состояние, которого быть не должно.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import readiness
from financial_ai.daily_ml.models import DailyMlRun
from financial_ai.daily_ml.repository import DailyMlRepository
from financial_ai.ranking import client as ranking_client
from financial_ai.ranking import dataset as dataset_module
from financial_ai.sync import advisory

logger = logging.getLogger(__name__)


def describe_failure(error: BaseException) -> tuple[str, str]:
    """Код и причина прерывания, пригодная для показа человеку.

    Текст исключения сюда не попадает намеренно: `repr` ошибки `httpx` несёт
    адрес обращения, а адрес может быть внутренним. Подробности не теряются —
    полный трейсбек пишет `logger.exception` там, где эта функция вызывается.
    """
    if isinstance(error, dataset_module.DatasetError):
        return "dataset_error", "не удалось собрать входной набор"
    if isinstance(error, ranking_client.RankingUnavailableError):
        return "ranking_unavailable", "звено ранжирования не ответило или ответило неверно"
    if isinstance(error, httpx.TimeoutException):
        return "timeout", "звено ранжирования не ответило вовремя"
    if isinstance(error, httpx.HTTPError):
        return "ranking_unavailable", "звено ранжирования недоступно"
    if isinstance(error, SQLAlchemyError):
        return "storage_error", "не удалось записать результат в хранилище"
    return "internal_error", "прогон прерван внутренней ошибкой"


async def execute(session: AsyncSession, settings: Settings, run: DailyMlRun) -> bool:
    """Выполнить одно задание. Возвращает, удалось ли оно."""
    repository = DailyMlRepository(session)
    now = dt.datetime.now(dt.UTC)

    await repository.mark_running(run.id, now)
    await session.commit()

    try:
        dataset = await dataset_module.build_dataset(session, settings, run.asof_date)

        # Окончательная проверка полноты. Дешёвая оценка могла устареть между
        # постановкой в очередь и выполнением, а инвариант нарушать нельзя.
        if not readiness.dataset_is_complete(dataset.incomplete, settings):
            await repository.mark_failed(
                run.id,
                dt.datetime.now(dt.UTC),
                "input_incomplete",
                "обязательный вход за эту дату неполон",
            )
            await session.commit()
            logger.info("ранжирование за %s отменено: вход неполон", run.asof_date)
            return False

        ranking = await ranking_client.request_ranking(settings, dataset)
    except Exception as error:
        code, message = describe_failure(error)
        await repository.mark_failed(run.id, dt.datetime.now(dt.UTC), code, message)
        await session.commit()
        logger.exception("ранжирование за %s не выполнено", run.asof_date)
        return False

    await repository.mark_success(
        run.id,
        dt.datetime.now(dt.UTC),
        [(item.rank, item.asset_id, item.price_series_id, item.score) for item in ranking.items],
        emulated=ranking.emulated,
    )
    await session.commit()

    logger.info(
        "ранжирование за %s выполнено: %d активов, модель %s %s",
        run.asof_date,
        len(ranking.items),
        ranking.model_id,
        run.model_version,
    )
    return True


async def process_queue(session: AsyncSession, settings: Settings, limit: int = 10) -> int:
    """Обработать очередь: по одной дате, от старой к новой.

    Блокировка берётся на всю обработку: два обработчика писали бы один и тот
    же результат и дважды тратили бы вычисления модели.
    """
    if not await advisory.try_acquire(session, advisory.DAILY_ML_OBJECT_ID):
        logger.debug("очередь ранжирования обрабатывается другим процессом")
        return 0

    processed = 0
    try:
        repository = DailyMlRepository(session)
        for _ in range(limit):
            run = await repository.next_queued()
            if run is None:
                break
            await execute(session, settings, run)
            processed += 1
    finally:
        await advisory.release(session, advisory.DAILY_ML_OBJECT_ID)
        await session.commit()

    return processed
