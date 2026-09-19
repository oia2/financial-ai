"""Планировщик сбора рыночных данных.

Раз в торговую сессию, после её закрытия. Не по таймеру «каждые N часов»:
дневные бары внутри сессии не меняются, и опрашивать биржу чаще нечего, а
раньше закрытия — опасно, незавершённая сессия в признаках это утечка будущего.

Остановка сбора устроена как пауза ранжирования: состояние живёт в процессе,
по умолчанию сбор включён, перезапуск возвращает его в работу (FR-029f). Это
РАЗНЫЕ переключатели — пауза ранжирования сбор данных не останавливает и
никогда не останавливала (FR-029e). Управляемый догон остановке не подчиняется:
это явная команда человека, и она сильнее общего выключателя (FR-029g).

Планировщик просыпается регулярно и спрашивает у **хранилища**, есть ли
несобранные закрытые сессии. Раньше он помнил это в памяти процесса, и память
давала два дефекта сразу: перезапуск после времени сбора приводил к повторному
сбору той же сессии, а неудачная попытка блокировала повтор до следующего дня.
Нужное состояние уже хранится — таблица исходов сбора знает и дату, и источник,
и статус.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from financial_ai.config import Settings
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import advance, groups, ingest, journal, plan
from financial_ai.market_data.calendar import moscow_now
from financial_ai.market_data.runner import CatchupState, CatchupStatus

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
        self._paused = False
        # Ход работы в той же форме, что у управляемого догона. Форма общая
        # намеренно: баннер процессов читает одно поле, и второй способ
        # рассказать об одном и том же однажды разошёлся бы с первым.
        self._state = CatchupState()
        self._stop_requested = False

    @property
    def paused(self) -> bool:
        return self._paused

    def request_stop(self) -> CatchupState:
        """Остановить идущий автоматический сбор.

        Мягко: текущая сессия доводится до конца, следующая не начинается. Это
        остановка ОДНОГО прогона, а не выключение автоматического режима — для
        второго есть пауза, и путать их нельзя: остановленный прогон возобновится
        на следующем тике, снятая пауза — нет.
        """
        if self._state.status is CatchupStatus.RUNNING:
            self._stop_requested = True
            self._state.status = CatchupStatus.STOPPING
            self._state.note_event("Запрошена остановка · идущий источник доводится до конца")
            logger.info("автоматический сбор: запрошена остановка")
        return self._state

    @property
    def state(self) -> CatchupState:
        """Ход автоматического сбора: та же форма, что у управляемого догона."""
        return self._state

    def set_paused(self, paused: bool) -> None:
        """Остановить или возобновить автоматический сбор.

        Останавливается создание НОВОЙ работы: начатая сессия доводится до конца,
        обрывать её на середине нельзя — день, собранный наполовину, неотличим от
        собранного полностью.
        """
        self._paused = paused
        logger.info(
            "автоматический сбор рыночных данных %s",
            "остановлен" if paused else "возобновлён",
        )

    async def start(self) -> None:
        if not self._settings.market_data_enabled:
            logger.info("сбор рыночных данных выключен настройкой")
            return

        # Прогон, не завершившийся из-за перезапуска, помечается прерванным.
        # Довести его было некому: тот, кто его вёл, больше не существует. Без
        # этой отметки он остался бы «идущим» навсегда (spec 008, FR-041).
        factory = get_session_factory()
        async with factory() as session:
            await journal.mark_interrupted(session, dt.datetime.now(dt.UTC))
            await session.commit()

        self._task = asyncio.create_task(self._loop(), name="market-data-scheduler")

    async def stop(self) -> None:
        """Остановка дожидается текущего прогона: обрывать сбор на середине нельзя."""
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._ingest_once()
            except Exception:
                logger.exception("сбор рыночных данных: прогон завершился ошибкой")

            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                continue

    async def _ingest_once(self) -> None:
        """Довести данные до последней закрытой сессии.

        Работа целиком в `advance`, и это не косвенность, а необходимость. Три
        вещи там неразделимы:

        - **календарь синхронизируется.** Пропуски считаются по сохранённому
          календарю, и пока он не обновлён, новых дат в нём нет, работы не
          видно и календарь не обновится. Прежняя версия звала только
          `pending_sessions` и на этом круге останавливалась: после простоя
          граница данных не двигалась вовсе;
        - **решение принимается по хранилищу**, а не по отметке в памяти:
          перезапуск не приводит к повторному сбору, а неудачная попытка не
          блокирует следующую в тот же день;
        - **разрыв собирается целиком за один проход.** Не «по три сессии за
          тик»: нарезка — тот же автоматический догон, только растянутый на
          несколько минут, и человек о нём не просил. Глубина ограничена окном
          догона — дальше сессия до модели не доходит. Если предел всё же задан
          числом и разрыв его превысил, не собирается ничего, и дыра уходит
          человеку.
        """
        if self._paused:
            return

        self._stop_requested = False

        def make_plan(days: list[dt.date]) -> None:
            # Состояние заводится, только когда работа действительно есть:
            # пустой план — обычный тик, и показывать по нему «идёт сбор»
            # значило бы мигать баннером 1438 раз в сутки.
            if not days:
                return
            self._state = CatchupState(
                status=CatchupStatus.RUNNING,
                mode=plan.MODE_DAILY,
                # Автоматический сбор идёт ПО ВСЕМ группам: `ingest_session`
                # собирает каждый источник за дату. Пустой список читался экраном
                # как «позиций тут нет», и он показывал оценку «котировки, 1–2 с
                # на сессию», пока на деле шли позиции по фьючерсам — около
                # 2,5 минуты на сессию. Обещание расходилось с работой в сто раз.
                group_ids=[group.group_id.value for group in groups.GROUPS],
                # Порядок ПОКАЗА — хронологический, каким бы ни был порядок
                # работы. Сбор берёт последнюю закрытую сессию первой (FR-045),
                # и если этот порядок положить как есть, диапазон в панели
                # окажется перевёрнутым, а шкала сессий пойдёт «сегодня, потом
                # самые старые» — её читают слева направо как хронологию.
                requested=sorted(days),
                date_from=min(days),
                date_till=max(days),
                started_at=dt.datetime.now(dt.UTC),
            )

        def session_start(day: dt.date) -> None:
            self._state.begin_session(day)

        def session_done(day: dt.date, succeeded: bool | str) -> None:
            """Исход сессии: собрана, не собрана либо прервана по команде.

            Прерванную доделывает продолжение, несобранную доберёт обычный план
            по правилам повторов. Прежде автоматический путь помечал прерванную
            несобранной, и продолжение её не брало (FR-058).
            """
            if succeeded == ingest.INTERRUPTED:
                self._state.failed.append(day)
                self._state.outcomes[day] = "partial"
            else:
                (self._state.closed if succeeded else self._state.failed).append(day)
                self._state.outcomes[day] = "collected" if succeeded else "failed"
            self._state.current = None

        def source_state(source_id: str, status: str, outcome: object) -> None:
            state = {"running": "running", "ok": "done", "failed": "failed"}.get(status, "skipped")
            detail = getattr(outcome, "failure_reason", None) if outcome is not None else None
            self._state.note_source(source_id, state, detail)

        def skipped(day: dt.date, reason: str, detail: str | None) -> None:
            self._state.note_skip(day, reason, detail)

        factory = get_session_factory()
        async with factory() as session:
            result = await advance.advance(
                session,
                self._settings,
                moscow_now(),
                on_plan=make_plan,
                on_session_start=session_start,
                on_session_done=session_done,
                on_source=source_state,
                on_skip=skipped,
                should_stop=lambda: self._stop_requested,
            )

        if self._state.status in (CatchupStatus.RUNNING, CatchupStatus.STOPPING):
            self._state.status = (
                CatchupStatus.STOPPED if self._stop_requested else CatchupStatus.FINISHED
            )
            self._state.note_event(
                "Прогон остановлен по команде" if self._stop_requested else "Прогон завершён"
            )
            self._state.finished_at = dt.datetime.now(dt.UTC)
        self._stop_requested = False

        if result.limit_exceeded:
            # Разрыв показан человеку — в сводке раздела и в состоянии
            # ранжирования — и закрывается управляемым догоном.
            return

        for day in result.pending:
            logger.warning("сессия %s не собрана полностью", day)

        # Данные за сессию собраны — самое время посмотреть, не появилась ли
        # работа у ранжирования. Ждать тика незачем.
        if result.collected:
            logger.info("сбор завершён за сессии: %s", ", ".join(str(d) for d in result.collected))
            await self._reconcile_daily_ml()

    async def _reconcile_daily_ml(self) -> None:
        """Сообщить ранжированию, что данные могли стать готовы.

        Импорт локальный: сбор данных не должен зависеть от звена ранжирования —
        оно может отсутствовать, и это не мешает собирать.
        """
        from financial_ai.daily_ml import reconcile as daily_ml_reconcile

        try:
            factory = get_session_factory()
            async with factory() as session:
                await daily_ml_reconcile.reconcile(session, self._settings)
        except Exception:
            # Сбой ранжирования не отменяет собранные данные: они уже записаны.
            logger.exception("реконсиляция ранжирования после сбора не выполнена")
