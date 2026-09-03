"""Планировщик сбора рыночных данных.

Раз в торговую сессию, после её закрытия. Не по таймеру «каждые N часов»:
дневные бары внутри сессии не меняются, и опрашивать биржу чаще нечего, а
раньше закрытия — опасно, незавершённая сессия в признаках это утечка будущего.

Планировщик просыпается регулярно и проверяет два условия: наступило ли время
после закрытия и не собрана ли уже эта сессия. Такой способ переживает
перезапуск контейнера в любой момент суток — в отличие от «поспать до 19:30».
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from financial_ai.config import Settings
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import ingest
from financial_ai.market_data.calendar import moscow_now

logger = logging.getLogger(__name__)

# Как часто просыпаться и проверять, не пора ли. Минута — компромисс между
# точностью запуска и бессмысленной работой.
TICK_SECONDS = 60.0


class MarketDataScheduler:
    """Фоновый цикл сбора рыночных данных."""

    def __init__(self, settings: Settings, tick_seconds: float = TICK_SECONDS) -> None:
        self._settings = settings
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_ingested_date: dt.date | None = None

    async def start(self) -> None:
        if not self._settings.market_data_enabled:
            logger.info("сбор рыночных данных выключен настройкой")
            return
        self._task = asyncio.create_task(self._loop(), name="market-data-scheduler")

    async def stop(self) -> None:
        """Остановка дожидается текущего прогона: обрывать сбор на середине нельзя."""
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None

    @staticmethod
    def _report_catchup(result: ingest.CatchupResult) -> None:
        """Догон не должен чинить молча: незакрытое видно так же, как сбой сбора."""
        if result.needs_backfill or result.skipped_reason is not None:
            logger.warning("догон не выполнялся: %s", result.skipped_reason)
            return
        if not result.attempted:
            return
        if result.failed:
            logger.warning(
                "догон: закрыто %d из %d, остались незакрытыми %s",
                len(result.closed),
                len(result.requested),
                ", ".join(day.isoformat() for day in result.failed),
            )
        else:
            logger.info("догон: закрыто сессий %d", len(result.closed))

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                if self._is_time_to_ingest(moscow_now()):
                    await self._ingest_once()
            except Exception:
                logger.exception("сбор рыночных данных: прогон завершился ошибкой")

            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                continue

    def _is_time_to_ingest(self, now: dt.datetime) -> bool:
        """Наступило ли время после закрытия сессии."""
        after = self._parse_time(self._settings.market_data_ingest_after_close)
        if now.time() < after:
            return False
        # Одна сессия — один сбор. Повторный прогон в тот же день не нужен:
        # дневные бары уже не изменятся.
        return self._last_ingested_date != now.date()

    @staticmethod
    def _parse_time(raw: str) -> dt.time:
        try:
            hours, minutes = raw.split(":", 1)
            return dt.time(int(hours), int(minutes))
        except (ValueError, IndexError):
            logger.warning("некорректное время запуска %r, используется 19:30", raw)
            return dt.time(19, 30)

    async def _ingest_once(self) -> None:
        factory = get_session_factory()
        async with factory() as session:
            result = await ingest.ingest_session(session, self._settings)

            # Догон после текущей сессии: она гейтит ранжирование и должна
            # пройти быстро. Отметка в памяти больше НЕ является знанием о
            # собранном — она лишь не даёт запускать цикл дважды в день.
            # Что собрано на самом деле, известно из данных.
            if result.session_date is not None:
                catchup = await ingest.catch_up(session, self._settings, result.session_date)
                self._report_catchup(catchup)

        # Отметка ставится независимо от исхода: повторять неудачный прогон
        # в тот же день бессмысленно, если биржа лежит. Следующая попытка —
        # на следующей сессии либо вручную.
        self._last_ingested_date = moscow_now().date()

        if result.unfinished_sources:
            logger.warning(
                "сбор за %s: незакрытые источники %s",
                result.session_date,
                ", ".join(result.unfinished_sources),
            )
        else:
            logger.info("сбор за %s завершён", result.session_date)
