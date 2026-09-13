"""Чтение и запись прогонов Daily ML.

Идемпотентность обеспечивается уникальным индексом, а не проверкой «сначала
посмотрим, потом вставим»: между двумя процессами такая проверка не атомарна.
Поэтому создание задания выполняется вставкой с обработкой конфликта — кто
успел, тот и создал, второй читает существующую запись.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.daily_ml.models import DailyMlRankingItem, DailyMlRun, RunStatus


@dataclass(frozen=True, slots=True)
class RunInput:
    """Идентичность прогона: четыре величины, и больше ничего."""

    asof_date: dt.date
    dataset_digest: str
    model_id: str
    model_version: str


class DailyMlRepository:
    """Доступ к прогонам и их результатам."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- создание и поиск ---------------------------------------------------

    async def find_by_input(self, run_input: RunInput) -> DailyMlRun | None:
        result = await self._session.execute(
            select(DailyMlRun).where(
                DailyMlRun.asof_date == run_input.asof_date,
                DailyMlRun.dataset_digest == run_input.dataset_digest,
                DailyMlRun.model_id == run_input.model_id,
                DailyMlRun.model_version == run_input.model_version,
            )
        )
        return result.scalar_one_or_none()

    async def enqueue(
        self,
        run_input: RunInput,
        dataset_ref: str,
        window: tuple[dt.date, dt.date] | None = None,
        input_complete: bool | None = None,
    ) -> tuple[DailyMlRun, bool]:
        """Поставить задание в очередь.

        Возвращает пару «запись, создана ли она сейчас». Конкурентный вызов не
        создаёт второго задания: конфликт уникальности гасится, и обе стороны
        получают одну и ту же запись.

        Окно и полнота входа записываются здесь, потому что здесь они известны:
        набор уже собран ради дайджеста. Вычислить их при показе нельзя —
        глубина окна это настройка, а полнота меняется с приходом данных.
        """
        statement = (
            insert(DailyMlRun)
            .values(
                asof_date=run_input.asof_date,
                dataset_digest=run_input.dataset_digest,
                dataset_ref=dataset_ref,
                model_id=run_input.model_id,
                model_version=run_input.model_version,
                status=RunStatus.QUEUED.value,
                attempt=1,
                window_from=window[0] if window else None,
                window_till=window[1] if window else None,
                input_complete=input_complete,
            )
            .on_conflict_do_nothing(constraint="uq_daily_ml_run_input")
            .returning(DailyMlRun.id)
        )
        created_id = (await self._session.execute(statement)).scalar_one_or_none()
        await self._session.flush()

        existing = await self.find_by_input(run_input)
        if existing is None:  # pragma: no cover — вставка либо прошла, либо был конфликт
            raise RuntimeError("задание не создано и не найдено")

        return existing, created_id is not None

    # --- очередь ------------------------------------------------------------

    async def next_queued(self) -> DailyMlRun | None:
        """Самое старое задание в очереди: даты обрабатываются по одной."""
        result = await self._session.execute(
            select(DailyMlRun)
            .where(DailyMlRun.status == RunStatus.QUEUED.value)
            .order_by(DailyMlRun.asof_date, DailyMlRun.id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def queued_runs(self) -> list[DailyMlRun]:
        result = await self._session.execute(
            select(DailyMlRun)
            .where(DailyMlRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]))
            .order_by(DailyMlRun.asof_date)
        )
        return list(result.scalars())

    async def queue_progress(self) -> tuple[int, int]:
        """Счётный прогресс очереди: сколько дат обработано из принятых.

        Отставание начинается с самой старой даты в очереди. Всё, что начиная с
        неё уже завершилось успехом, обработано; всё, что ещё стоит, — нет.
        Числа считаются здесь, а не в браузере: это факт о хранилище, а не
        наблюдение экрана, и после перезагрузки страницы он не должен меняться.

        Пустая очередь даёт `(0, 0)` — прогресса нет, потому что нет работы.
        """
        pending = await self.queued_runs()
        if not pending:
            return 0, 0

        oldest = min(run.asof_date for run in pending)
        completed = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(DailyMlRun)
                    .where(
                        DailyMlRun.status == RunStatus.SUCCESS.value,
                        DailyMlRun.asof_date >= oldest,
                    )
                )
            ).scalar_one()
        )
        return completed, completed + len(pending)

    async def running_run(self) -> DailyMlRun | None:
        result = await self._session.execute(
            select(DailyMlRun).where(DailyMlRun.status == RunStatus.RUNNING.value).limit(1)
        )
        return result.scalar_one_or_none()

    # --- переходы состояний -------------------------------------------------

    async def mark_running(self, run_id: int, now: dt.datetime) -> None:
        await self._session.execute(
            update(DailyMlRun)
            .where(DailyMlRun.id == run_id)
            .values(status=RunStatus.RUNNING.value, started_at=now, finished_at=None)
        )

    async def mark_failed(self, run_id: int, now: dt.datetime, code: str, message: str) -> None:
        await self._session.execute(
            update(DailyMlRun)
            .where(DailyMlRun.id == run_id)
            .values(
                status=RunStatus.FAILED.value,
                finished_at=now,
                error_code=code,
                error_message=message,
            )
        )

    async def mark_success(
        self,
        run_id: int,
        now: dt.datetime,
        items: list[tuple[int, str, str, Decimal]],
        emulated: bool | None = None,
    ) -> None:
        """Записать результат и перевести прогон в успешный.

        Одной транзакцией: половина выдачи хуже её отсутствия, и «успешно, но
        без строк» — состояние, которого быть не должно.
        """
        self._session.add_all(
            [
                DailyMlRankingItem(
                    run_id=run_id,
                    rank=rank,
                    asset_id=asset_id,
                    price_series_id=price_series_id,
                    score=score,
                )
                for rank, asset_id, price_series_id, score in items
            ]
        )
        await self._session.execute(
            update(DailyMlRun)
            .where(DailyMlRun.id == run_id)
            .values(
                status=RunStatus.SUCCESS.value,
                finished_at=now,
                error_code=None,
                error_message=None,
                included_asset_count=len(items),
                # Признак приходит из ответа звена: оно само объявляет себя
                # эмулятором. Догадка по имени модели разошлась бы с правдой
                # молча, и предупреждение о вымышленных скорах исчезло бы.
                emulated=emulated,
            )
        )

    async def requeue(self, run_id: int) -> None:
        """Повтор: попытка увеличивается, следы прошлого отказа стираются."""
        await self._session.execute(
            update(DailyMlRun)
            .where(DailyMlRun.id == run_id)
            .values(
                status=RunStatus.QUEUED.value,
                attempt=DailyMlRun.attempt + 1,
                started_at=None,
                finished_at=None,
                error_code=None,
                error_message=None,
            )
        )

    async def fail_interrupted(self, now: dt.datetime) -> list[int]:
        """Пометить прогоны, оборванные перезапуском.

        Обработчик один: `running` при старте однозначно означает обрыв. Без
        этого шага запись осталась бы «в работе» навсегда — ровно та болезнь,
        от которой догон ушёл, отказавшись хранить состояние.

        Возвращает идентификаторы: вызывающий решает, какие из них вернуть в
        очередь. Обрыв — не отказ модели и не порок входа, поэтому по умолчанию
        работа возобновляется, а не ждёт человека.
        """
        touched = await self._session.scalars(
            update(DailyMlRun)
            .where(DailyMlRun.status == RunStatus.RUNNING.value)
            .values(
                status=RunStatus.FAILED.value,
                finished_at=now,
                error_code="interrupted",
                error_message="прогон прерван перезапуском сборщика",
            )
            .returning(DailyMlRun.id)
        )
        return list(touched.all())

    # --- чтение -------------------------------------------------------------

    async def latest_success(self) -> DailyMlRun | None:
        result = await self._session.execute(
            select(DailyMlRun)
            .where(DailyMlRun.status == RunStatus.SUCCESS.value)
            .order_by(DailyMlRun.asof_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_success_for_date(self, asof_date: dt.date) -> DailyMlRun | None:
        """Успешный прогон за конкретную дату решения, самый свежий."""
        result = await self._session.execute(
            select(DailyMlRun)
            .where(
                DailyMlRun.asof_date == asof_date,
                DailyMlRun.status == RunStatus.SUCCESS.value,
            )
            .order_by(DailyMlRun.finished_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def last_failure(self) -> DailyMlRun | None:
        result = await self._session.execute(
            select(DailyMlRun)
            .where(DailyMlRun.status == RunStatus.FAILED.value)
            .order_by(DailyMlRun.finished_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get(self, run_id: int) -> DailyMlRun | None:
        return await self._session.get(DailyMlRun, run_id)

    async def history(
        self, status: str | None = None, limit: int = 10, offset: int = 0
    ) -> tuple[list[DailyMlRun], int]:
        condition = DailyMlRun.status == status if status else None

        total_statement = select(func.count()).select_from(DailyMlRun)
        statement = select(DailyMlRun).order_by(DailyMlRun.asof_date.desc(), DailyMlRun.id.desc())
        if condition is not None:
            total_statement = total_statement.where(condition)
            statement = statement.where(condition)

        total = int((await self._session.execute(total_statement)).scalar_one())
        rows = await self._session.execute(statement.limit(limit).offset(offset))
        return list(rows.scalars()), total

    async def items(
        self, run_id: int, limit: int | None = None, offset: int = 0
    ) -> list[DailyMlRankingItem]:
        statement = (
            select(DailyMlRankingItem)
            .where(DailyMlRankingItem.run_id == run_id)
            .order_by(DailyMlRankingItem.rank)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars())
