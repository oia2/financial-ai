"""Фоновая синхронизация состояния счёта.

Одна asyncio-задача вместо планировщика: задача ровно одна, cron-выражения и
персистентный job store не нужны, а динамическая смена интервала выражается
одной строкой — перечитыванием значения из БД в начале каждого цикла.

Свойства конструкции:

* FR-031, SC-012 — новый интервал применяется к следующему циклу без
  перезапуска, потому что источник истины один: БД;
* FR-033 — циклы не накладываются и не копятся: следующая итерация не
  начинается, пока не завершилась предыдущая, очереди нет;
* FR-032 — ошибка брокера не останавливает цикл.
"""

from __future__ import annotations

import asyncio
import logging

from financial_ai.db.engine import session_scope
from financial_ai.db.settings_repo import get_interval_seconds
from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.service import SyncResult, SyncService

logger = logging.getLogger(__name__)

# Интервал, применяемый, если прочитать настройку не удалось: цикл не должен
# остановиться из-за временной недоступности БД.
FALLBACK_INTERVAL_SECONDS = 60


class SyncScheduler:
    """Фоновый цикл синхронизации."""

    def __init__(self, service: SyncService, single_flight: SingleFlight[SyncResult]) -> None:
        self._service = service
        self._single_flight = single_flight
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._current_interval: int | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_interval_seconds(self) -> int | None:
        """Интервал, применённый к текущему циклу."""
        return self._current_interval

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="sync-scheduler")

    async def stop(self) -> None:
        """Останавливает цикл, дождавшись текущей синхронизации.

        Обрывать транзакцию на середине нельзя: состояние должно остаться
        целым (FR-008).
        """
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def run_once(self) -> tuple[SyncResult, bool]:
        """Одна синхронизация под общим локом — точка входа и для ручного запуска."""
        return await self._single_flight.run(self._service.sync_account_state)

    async def _run(self) -> None:
        logger.info("Фоновая синхронизация запущена")

        while not self._stop.is_set():
            interval = await self._read_interval()
            self._current_interval = interval

            try:
                await self.run_once()
            except Exception:
                # Цикл не прекращается из-за ошибок: следующая попытка будет
                # в следующем интервале (FR-032).
                logger.exception("Непредвиденная ошибка цикла синхронизации")

            await self._wait(interval)

        logger.info("Фоновая синхронизация остановлена")

    async def _read_interval(self) -> int:
        try:
            async with session_scope() as session:
                return await get_interval_seconds(session)
        except Exception:
            logger.exception("Не удалось прочитать интервал обновления, используется резервный")
            return FALLBACK_INTERVAL_SECONDS

    async def _wait(self, seconds: int) -> None:
        """Ожидание, прерываемое остановкой процесса."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return
