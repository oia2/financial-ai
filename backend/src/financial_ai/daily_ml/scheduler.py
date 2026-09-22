"""Планировщик жизненного цикла Daily ML.

Просыпается регулярно и делает две вещи: ищет работу и выполняет очередь.
Такой способ переживает перезапуск в любой момент — в отличие от «поспать до
события», и по той же причине, по которой так устроен сбор рыночных данных.

Пауза останавливает **создание** заданий, а не выполнение: начатый прогон
доводится до конца. Пауза при этом не останавливает сбор рыночных данных —
это разные механизмы, и интерфейс обязан об этом говорить.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from financial_ai.config import Settings
from financial_ai.daily_ml import reconcile as reconcile_module
from financial_ai.daily_ml import runner
from financial_ai.db.engine import get_session_factory

logger = logging.getLogger(__name__)


class DailyMlScheduler:
    """Фоновый цикл ранжирования."""

    def __init__(
        self,
        settings: Settings,
        tick_seconds: float | None = None,
        collection_active: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings
        # Идёт ли прямо сейчас сбор рыночных данных. Предикат, а не флаг:
        # владелец состояния — сборщик, и второй его копии здесь не заводится.
        self._collection_active = collection_active or (lambda: False)
        self._tick_seconds = tick_seconds or settings.daily_ml_tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._paused = False
        # Последний результат реконсиляции. Держится, чтобы состояние раздела
        # отдавалось из него, а не пересчитывалось на каждый опрос: пересборка
        # набора ради дайджеста стоит секунд процессорного времени.
        self._last: reconcile_module.ReconcileResult | None = None

    @property
    def last_result(self) -> reconcile_module.ReconcileResult | None:
        return self._last

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        logger.info("автоматическое ранжирование %s", "на паузе" if paused else "возобновлено")

    async def start(self) -> None:
        if not self._settings.daily_ml_enabled:
            logger.info("автоматическое ранжирование выключено настройкой")
            return

        # Первый тик выполняется фоновым циклом. Ранжирование не является
        # условием готовности worker: ожидание пересборки набора здесь не должно
        # задерживать ни healthcheck, ни чтение уже собранных рыночных данных.
        # _loop делает тик сразу, до первой паузы.
        self._task = asyncio.create_task(self._loop(), name="daily-ml-scheduler")

    async def stop(self) -> None:
        """Остановка дожидается текущего прогона: обрывать его нельзя."""
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("цикл ранжирования: тик завершился ошибкой")

            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                continue

    async def tick(self) -> reconcile_module.ReconcileResult:
        """Один проход: найти работу и выполнить очередь."""
        factory = get_session_factory()
        async with factory() as session:
            # Пока идёт сбор, работа НЕ ищется. Готовая дата в этот момент —
            # движущаяся цель: сессии закрываются от старых к новым, и окно
            # ранней даты становится полным раньше, чем окно последней. Тик
            # ранжирования, попавший в середину догона, ставил задание на 04.09
            # и считал порядок активов по дате, которая последней быть перестала
            # через минуту. Наблюдалось на стенде.
            if self._collection_active():
                logger.debug("идёт сбор данных: поиск работы отложен до его окончания")
                return self._carry(
                    reconcile_module.ReconcileResult(notes=["идёт сбор рыночных данных"])
                )

            result = await reconcile_module.reconcile(session, self._settings, self._paused)

            if not self._paused:
                await runner.process_queue(session, self._settings)

        return self._carry(result)

    def _carry(self, result: reconcile_module.ReconcileResult) -> reconcile_module.ReconcileResult:
        """Запомнить исход, не забыв того, что уже было известно.

        Пауза и отложенный тик до вопроса об устаревании не доходят, и прошлый
        ответ остаётся последним, что система об этом знает: затирать его на
        `None` значило бы забыть наблюдение, а не обновить его.
        """
        if result.stale_latest is None and self._last is not None:
            result = replace(result, stale_latest=self._last.stale_latest)

        self._last = result
        return result
