"""Догон как управляемая фоновая задача.

Догон больше не запускается сам: ежедневный цикл собирает текущую сессию, а
историю добирает человек. Один прогон на живых данных показал, почему —
неуправляемый догон ушёл на 2909 обращений к бирже без спроса и без возможности
вмешаться.

Два решения, на которых держится модуль:

- **состояние живёт в этом процессе, а не в хранилище.** Перезапуск снимает
  «идёт» сам собой, и зависшего состояния не бывает по устройству. Хранимый
  признак пришлось бы сторожить: процесс упал, строка осталась, догон больше не
  запускается;
- **остановка мягкая.** Признак проверяется МЕЖДУ сессиями: день, собранный
  наполовину, неотличим от собранного полностью, и обрывать сессию нельзя.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from enum import StrEnum

import httpx
from sqlalchemy.exc import SQLAlchemyError

from financial_ai.config import Settings
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import gaps, groups, ingest
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.iss.client import IssError
from financial_ai.market_data.repository import MarketDataRepository

logger = logging.getLogger(__name__)


def describe_failure(error: BaseException) -> str:
    """Причина прерывания в виде, пригодном для показа человеку.

    Текст исключения сюда не попадает намеренно. `repr` ошибки `httpx` несёт
    адрес, по которому шло обращение, а он может быть внутренним; на экране
    это раскрывало бы конфигурацию развёртывания (FR-005a фичи 005, FR-043
    фичи 006). Подробности не теряются: полный трейсбек пишет
    `logger.exception` строкой ниже места, где эта функция вызывается.
    """
    if isinstance(error, IssError):
        return "источник данных ответил ошибкой"
    if isinstance(error, httpx.TimeoutException):
        return "источник данных не ответил вовремя"
    if isinstance(error, httpx.HTTPError):
        return "источник данных недоступен"
    if isinstance(error, SQLAlchemyError):
        return "не удалось записать собранное в хранилище"
    return "прогон прерван внутренней ошибкой"


class CatchupStatus(StrEnum):
    """Состояние задания догона."""

    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FINISHED = "finished"
    FAILED = "failed"


class CatchupAlreadyRunningError(RuntimeError):
    """Догон уже идёт.

    Отклоняем, а не присоединяемся: два одновременных сбора писали бы одни и те
    же строки и удваивали обращения к бирже.
    """


class BackfillRequiredError(RuntimeError):
    """В хранилище нет наблюдений: нужна первичная загрузка, а не догон."""


class NothingToCatchUpError(RuntimeError):
    """Пропущенных сессий в выбранном диапазоне нет."""


@dataclass(slots=True)
class CatchupState:
    """Снимок состояния задания."""

    status: CatchupStatus = CatchupStatus.IDLE
    group_ids: list[str] = field(default_factory=list)
    date_from: dt.date | None = None
    date_till: dt.date | None = None
    clamped: bool = False
    requested: list[dt.date] = field(default_factory=list)
    closed: list[dt.date] = field(default_factory=list)
    failed: list[dt.date] = field(default_factory=list)
    current: dt.date | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    reason: str | None = None

    @property
    def remaining(self) -> int:
        return len(self.requested) - len(self.closed) - len(self.failed)

    def snapshot(self) -> dict[str, object]:
        """Состояние в виде, пригодном для ответа контракта."""
        return {
            "status": self.status.value,
            "groups": list(self.group_ids),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_till": self.date_till.isoformat() if self.date_till else None,
            "clamped": self.clamped,
            "requested": len(self.requested),
            "closed": len(self.closed),
            "failed": len(self.failed),
            "remaining": max(self.remaining, 0),
            "current": self.current.isoformat() if self.current else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "reason": self.reason,
        }


class CatchupRunner:
    """Владелец фоновой задачи догона.

    Один экземпляр на процесс: единственность задания обеспечивается им, а не
    блокировкой в хранилище.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._state = CatchupState()
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = False

    # --- чтение -------------------------------------------------------------

    def status(self) -> dict[str, object]:
        return self._state.snapshot()

    @property
    def is_active(self) -> bool:
        return self._state.status in (CatchupStatus.RUNNING, CatchupStatus.STOPPING)

    # --- управление ---------------------------------------------------------

    async def start(
        self,
        group_ids: list[str] | None = None,
        date_from: dt.date | None = None,
        date_till: dt.date | None = None,
    ) -> dict[str, object]:
        """Запустить догон. Бросает, если он уже идёт или запускать нечего."""
        if self.is_active:
            raise CatchupAlreadyRunningError("догон уже выполняется")

        selected = groups.resolve(group_ids)

        plan = await self._plan(selected, date_from, date_till)

        self._stop_requested = False
        self._state = CatchupState(
            status=CatchupStatus.RUNNING,
            group_ids=[group.group_id.value for group in selected],
            date_from=plan[0][0],
            date_till=plan[0][-1],
            clamped=plan[1],
            requested=list(plan[0]),
            started_at=dt.datetime.now(dt.UTC),
        )

        self._task = asyncio.create_task(self._run(selected), name="market-data-catchup")
        return self._state.snapshot()

    def stop(self) -> dict[str, object]:
        """Попросить остановиться. Текущая сессия доводится до конца."""
        if not self.is_active:
            return self._state.snapshot()

        self._stop_requested = True
        self._state.status = CatchupStatus.STOPPING
        logger.info("догон: запрошена остановка на сессии %s", self._state.current)
        return self._state.snapshot()

    async def shutdown(self) -> None:
        """Дождаться задачи при остановке компонента."""
        self._stop_requested = True
        if self._task is not None:
            await self._task
            self._task = None

    # --- внутреннее ---------------------------------------------------------

    async def _plan(
        self,
        selected: tuple[groups.SourceGroup, ...],
        date_from: dt.date | None,
        date_till: dt.date | None,
    ) -> tuple[list[dt.date], bool]:
        """Определить, какие сессии предстоит собрать."""
        factory = get_session_factory()
        async with factory() as session:
            repository = MarketDataRepository(session)
            calendar = TradingCalendar(repository)

            asof = await calendar.latest_session(moscow_today())
            if asof is None:
                raise NothingToCatchUpError("календарь пуст: собирать нечего")

            report = await gaps.find_gaps(session, self._settings, asof)

        if report.needs_backfill:
            raise BackfillRequiredError("в хранилище нет наблюдений: нужна первичная загрузка")

        sessions, clamped = _clamp(report.missing_sessions, date_from, date_till)
        if not sessions:
            raise NothingToCatchUpError("пропущенных сессий нет")

        logger.info(
            "догон: к сбору %d сессий, группы %s",
            len(sessions),
            ", ".join(group.group_id.value for group in selected),
        )
        return sessions, clamped

    async def _run(self, selected: tuple[groups.SourceGroup, ...]) -> None:
        """Тело фоновой задачи."""
        factory = get_session_factory()
        try:
            async with factory() as session:
                result = await ingest.catch_up(
                    session,
                    self._settings,
                    self._state.requested[-1],
                    sessions=self._state.requested,
                    source_ids=groups.source_ids_for(selected),
                    on_session_start=self._on_session_start,
                    on_session_done=self._on_session_done,
                    should_stop=lambda: self._stop_requested,
                )
        except Exception as error:
            self._state.status = CatchupStatus.FAILED
            self._state.reason = describe_failure(error)
            self._state.finished_at = dt.datetime.now(dt.UTC)
            logger.exception("догон завершился ошибкой")
            return

        self._state.closed = list(result.closed)
        self._state.failed = list(result.failed)
        self._state.current = None
        self._state.finished_at = dt.datetime.now(dt.UTC)
        self._state.status = (
            CatchupStatus.STOPPED if self._stop_requested else CatchupStatus.FINISHED
        )

        logger.info(
            "догон завершён: %s, закрыто %d из %d",
            self._state.status.value,
            len(result.closed),
            len(self._state.requested),
        )

    def _on_session_start(self, day: dt.date) -> None:
        self._state.current = day

    def _on_session_done(self, day: dt.date, closed: bool) -> None:
        if closed:
            self._state.closed.append(day)
        else:
            self._state.failed.append(day)


def _clamp(
    sessions: list[dt.date], date_from: dt.date | None, date_till: dt.date | None
) -> tuple[list[dt.date], bool]:
    """Обрезать выбранный диапазон по окну.

    Обрезка возвращается вызывающему: сессия старше окна в набор всё равно не
    попадёт, и человек должен видеть, что его диапазон сузили.
    """
    if not sessions:
        return [], False

    inside = [
        day
        for day in sessions
        if (date_from is None or day >= date_from) and (date_till is None or day <= date_till)
    ]

    outside_requested = (date_from is not None and date_from < sessions[0]) or (
        date_till is not None and date_till > sessions[-1]
    )
    return inside, outside_requested
