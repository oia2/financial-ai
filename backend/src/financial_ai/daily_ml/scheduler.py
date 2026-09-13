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
from dataclasses import replace

from financial_ai.config import Settings
from financial_ai.daily_ml import reconcile as reconcile_module
from financial_ai.daily_ml import runner
from financial_ai.db.engine import get_session_factory

logger = logging.getLogger(__name__)


class DailyMlScheduler:
    """Фоновый цикл ранжирования."""

    def __init__(self, settings: Settings, tick_seconds: float | None = None) -> None:
        self._settings = settings
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

        # Восстановление при старте: прерванные прогоны и отставание.
        #
        # Сбой этого прохода **не должен останавливать worker**. Ранжирование —
        # одна из его работ, а не условие его существования: недоступное звено,
        # неудавшаяся сборка набора или нехватка прав на том наборов не отменяют
        # сбора рыночных данных и синхронизации счёта. Без этой защиты падение
        # первого же тика уносило весь контейнер, и данные переставали
        # собираться из-за неисправности, к ним не относящейся.
        try:
            await self.tick()
        except Exception:
            logger.exception("восстановление ранжирования при старте не выполнено")

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
            result = await reconcile_module.reconcile(session, self._settings, self._paused)

            if not self._paused:
                await runner.process_queue(session, self._settings)

        # Пауза до вопроса об устаревании не доходит, и прошлый ответ остаётся
        # последним, что система об этом знает: затирать его на `None` значило
        # бы забыть наблюдение, а не обновить его.
        if result.stale_latest is None and self._last is not None:
            result = replace(result, stale_latest=self._last.stale_latest)

        self._last = result
        return result
