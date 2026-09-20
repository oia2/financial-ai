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
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

import httpx
from sqlalchemy.exc import SQLAlchemyError

from financial_ai.config import Settings
from financial_ai.db.engine import get_session_factory
from financial_ai.market_data import completeness, gaps, groups, ingest
from financial_ai.market_data import plan as plan_module
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


# Сколько пропусков отдавать в состоянии прогона. Остальные считаются числом:
# длинный прогон даёт сотни, и раскрытый список без потолка не листается.
# Исход источника → состояние в ленте. Пропуск обращения остаётся пропуском,
# а `omitted` убирает строку целиком: см. `CatchupState.note_source`.
RAIL_STATE = {
    "running": "running",
    "ok": "done",
    "failed": "failed",
    ingest.STATUS_OMITTED: ingest.STATUS_OMITTED,
}

SKIPS_SHOWN = 50

# Сколько событий прогона держать. Журнал отвечает на вопрос «что было
# последние минуты», а не хранит всю историю прогона: для неё есть таблица
# исходов сбора.
LOG_KEPT = 40


def _suffix(detail: str | None) -> str:
    """Подробность события, если источник её дал."""
    return f" · {detail}" if detail else ""


@dataclass(slots=True)
class CatchupState:
    """Снимок состояния задания.

    Кроме счётчиков состояние несёт **план источников текущей сессии** и
    **причины пропусков**. Без первого долгий источник неотличим от зависания;
    без второго человек видит дыру и не знает, ждать ему или вмешиваться
    (spec 008, FR-002, FR-003).
    """

    status: CatchupStatus = CatchupStatus.IDLE
    mode: str = plan_module.MODE_DAILY
    group_ids: list[str] = field(default_factory=list)
    date_from: dt.date | None = None
    date_till: dt.date | None = None
    clamped: bool = False
    requested: list[dt.date] = field(default_factory=list)

    # Порядок, в котором прогон БЕРЁТ сессии. Порядок показа хронологический
    # (FR-026), а ежедневный цикл идёт от свежих к старым (FR-045) — и
    # продолжение, построенное по порядку показа, прыгало на другой конец
    # плана: остановили на 22 июля, продолжили с 18 июня (FR-058g).
    order: list[dt.date] = field(default_factory=list)
    closed: list[dt.date] = field(default_factory=list)
    failed: list[dt.date] = field(default_factory=list)

    # Исход каждой сессии плана: collected | partial | failed | skipped.
    outcomes: dict[dt.date, str] = field(default_factory=dict)

    # Пропуски с причиной: (дата, причина, пояснение).
    skips: list[tuple[dt.date, str, str | None]] = field(default_factory=list)

    # Состояние источников ТЕКУЩЕЙ сессии: source_id → (состояние, подробность).
    # Счёт идёт внутри одной сессии: следующая пройдёт тот же план заново.
    sources: dict[str, tuple[str, str | None]] = field(default_factory=dict)

    # Источники, которых этот прогон не спрашивает вовсе. Суточный источник
    # спрашивается раз в сутки, и в повторном прогоне того же дня строка «уже
    # спрошен сегодня» отвечает на вопрос, которого никто не задавал: план —
    # это то, что прогон делает, а не перечень всего, что бывает (FR-007).
    omitted: set[str] = field(default_factory=set)

    # Журнал событий прогона: (момент, что произошло). Отвечает на вопрос «что
    # было последние минуты» — тот, на который лента источников не отвечает:
    # она показывает только НЫНЕШНЕЕ положение дел, а произошедшее стирает.
    log: list[tuple[dt.datetime, str]] = field(default_factory=list)

    current: dt.date | None = None

    # Идентификатор прогона: по нему продолжение узнаёт собранное им же.
    run_id: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    last_response_at: dt.datetime | None = None
    stop_requested: bool = False
    reason: str | None = None

    @property
    def remaining(self) -> int:
        return len(self.requested) - len(self.closed) - len(self.failed)

    @property
    def unfinished(self) -> list[dt.date]:
        """Сессии плана, которые продолжение обязано доделать.

        Не только те, которых не начинали. Сессия, чей план источников не
        доработан до конца, тоже непройденная: остановка после котировок, но до
        агрегатов, оставляла сессию с исходом — и продолжение брало следующую,
        а недобранные агрегаты не добирало никогда (FR-058).

        Пропущенная сессия сюда не входит: у пропуска есть причина, и
        продолжение не должно спорить с ней молча. Несобранная — тоже: её
        доберёт обычный план по правилам повторов, а тащить её в продолжение
        значило бы перевыбирать её вечно.
        """
        plan = self.order or self.requested
        return [day for day in plan if self.outcomes.get(day) in (None, "partial")]

    def note_source(self, source_id: str, state: str, detail: str | None = None) -> None:
        """Отметить состояние источника и момент последнего ответа.

        Состояние ``omitted`` означает «этот прогон его не спрашивает» и
        убирает источник из плана. Применяется только к тому, чего прогон ещё
        ни разу не касался: суточный источник, спрошенный на первой сессии,
        остаётся в плане до конца прогона со своим исходом (FR-057).
        """
        if state == ingest.STATUS_OMITTED:
            if source_id not in self.sources:
                self.omitted.add(source_id)
            return

        self.omitted.discard(source_id)
        previous = self.sources.get(source_id)
        self.sources[source_id] = (state, detail)
        if state != "running":
            self.last_response_at = dt.datetime.now(dt.UTC)

        # В журнал попадает СМЕНА состояния, а не каждый вызов: иначе «идёт»
        # писалось бы перед каждым обращением и вытеснило бы всё остальное.
        if previous is not None and previous[0] == state:
            return
        if state == "done":
            self.note_event(f"{plan_module.title_of(source_id)} · собран{_suffix(detail)}")
        elif state == "failed":
            self.note_event(f"{plan_module.title_of(source_id)} · не отдал данные{_suffix(detail)}")

    def note_event(self, text: str) -> None:
        """Записать событие. Хвост обрезается: журнал — не бесконечная лента."""
        self.log.append((dt.datetime.now(dt.UTC), text))
        if len(self.log) > LOG_KEPT:
            del self.log[:-LOG_KEPT]

    def begin_session(self, day: dt.date) -> None:
        """Новая сессия — ПОСЕССИОННЫЙ план начинается заново.

        Диапазонные и суточные источники при этом сохраняют своё состояние:
        они идут ОДИН раз на прогон, перед циклом сессий, и стирать их вместе с
        посессионными значило бы держать их ожидающими до конца прогона.
        Именно так «Глобальные ряды» и «Курсы и ставка ЦБ» и выглядели весь
        ручной прогон — тем же способом, каким до FR-056 висел ожидающим
        торговый календарь (FR-057).
        """
        if self.current is not None and self.current != day:
            outcome = self.outcomes.get(self.current)
            if outcome == "failed":
                self.note_event(f"Сессия {self.current:%d.%m} собрана не полностью")
            elif outcome == "collected":
                self.note_event(f"Сессия {self.current:%d.%m} собрана")

        self.current = day
        per_session = {
            spec.source_id
            for spec in plan_module.for_mode(self.mode)
            if spec.scope == plan_module.SESSION
        }
        self.sources = {
            source_id: state
            for source_id, state in self.sources.items()
            if source_id not in per_session
        }

    def note_skip(self, day: dt.date, reason: str, detail: str | None = None) -> None:
        self.outcomes[day] = "skipped"
        self.skips.append((day, reason, detail))
        self.note_event(f"Сессия {day:%d.%m} пропущена: {detail or reason}")

    def _session_plan(self) -> list[dict[str, object]]:
        """План источников текущей сессии с состоянием каждого."""
        rows: list[dict[str, object]] = []
        for spec in plan_module.for_mode(self.mode):
            if spec.source_id in self.omitted:
                continue
            state, detail = self.sources.get(spec.source_id, ("pending", None))
            row: dict[str, object] = {
                "source_id": spec.source_id,
                "title": spec.title,
                "scope": spec.scope,
                "state": state,
            }
            if detail:
                row["detail"] = detail
            rows.append(row)
        return rows

    def snapshot(self) -> dict[str, object]:
        """Состояние в виде, пригодном для ответа контракта."""
        counts = {"collected": 0, "partial": 0, "failed": 0, "skipped": 0}
        for outcome in self.outcomes.values():
            if outcome in counts:
                counts[outcome] += 1

        return {
            "status": self.status.value,
            "mode": self.mode,
            "groups": list(self.group_ids),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_till": self.date_till.isoformat() if self.date_till else None,
            "clamped": self.clamped,
            "sessions": {
                "requested": len(self.requested),
                **counts,
                "pending": max(len(self.requested) - sum(counts.values()), 0),
                "outcomes": [
                    {"session_date": day.isoformat(), "outcome": self.outcomes[day]}
                    for day in self.requested
                    if day in self.outcomes
                ],
            },
            # Пропусков за длинный прогон бывают сотни. Раскрытый список без
            # потолка — это страница, которую невозможно долистать; счётчик
            # рядом честнее, чем все строки разом.
            # Свежие сверху: человек читает журнал сверху вниз и первым должен
            # увидеть последнее, а не то, что было полчаса назад.
            "log": [
                {"at": moment.isoformat(), "text": text} for moment, text in reversed(self.log)
            ],
            "skips_total": len(self.skips),
            "skips": [
                {"session_date": day.isoformat(), "reason": reason, "detail": detail}
                for day, reason, detail in self.skips[-SKIPS_SHOWN:]
            ],
            "current": (
                {"session_date": self.current.isoformat(), "sources": self._session_plan()}
                if self.current
                else None
            ),
            # Старые поля контракта фичи 005: интерфейс переезжает на новые, но
            # ломать его одномоментно незачем.
            "requested": len(self.requested),
            "closed": len(self.closed),
            "failed": len(self.failed),
            "remaining": max(self.remaining, 0),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "last_response_at": (
                self.last_response_at.isoformat() if self.last_response_at else None
            ),
            "stop_requested": self.stop_requested,
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
    def state(self) -> CatchupState:
        """Состояние прогона как оно есть — не снимок для ответа.

        Нужно продолжению: оно читает непройденные сессии остановленного
        прогона, а в снимке их нет и быть не должно (FR-058).
        """
        return self._state

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

        planned = await self._plan(selected, date_from, date_till)

        self._stop_requested = False
        self._state = CatchupState(
            status=CatchupStatus.RUNNING,
            mode=plan_module.MODE_MANUAL,
            group_ids=[group.group_id.value for group in selected],
            date_from=planned[0][0],
            date_till=planned[0][-1],
            clamped=planned[1],
            requested=list(planned[0]),
            order=list(planned[0]),
            # Сессия называется СРАЗУ, а не когда до неё дошла очередь: до неё
            # прогон синхронизирует календарь и состав инструментов — видимую
            # работу, — а лента источников без названной сессии на экран не
            # выходит вовсе (FR-058c).
            current=planned[0][0],
            run_id=str(uuid.uuid4()),
            started_at=dt.datetime.now(dt.UTC),
        )

        self._task = asyncio.create_task(self._run(selected), name="market-data-catchup")
        return self._state.snapshot()

    async def resume(self, stopped: CatchupState) -> dict[str, object]:
        """Продолжить остановленный прогон В ЕГО ЖЕ состоянии.

        Остановка — пауза, а не отмена: счётчик сессий продолжает свой счёт,
        пройденные остаются пройденными, журнал событий не обнуляется, лента
        источников не мигает. Прежде продолжение заводило новое состояние, и
        всё, что человек видел до остановки, начиналось с нуля (FR-058b).

        Состояние КОПИРУЕТСЯ, а не перенимается: остановить можно и
        автоматический прогон, а его состоянием владеет планировщик, и писать
        в него вдвоём нельзя.
        """
        if self.is_active:
            raise CatchupAlreadyRunningError("догон уже выполняется")

        pending = stopped.unfinished
        if not pending:
            raise NothingToCatchUpError("непройденных сессий в прогоне нет")

        selected = groups.resolve(stopped.group_ids or None)

        self._stop_requested = False
        self._state = CatchupState(
            status=CatchupStatus.RUNNING,
            # Режим — прежний. У ежедневного сбора и ручного догона планы
            # разные, и перевод в ручной менял план под человеком: часть строк
            # исчезала с экрана посреди работы (FR-058b).
            mode=stopped.mode,
            group_ids=list(stopped.group_ids),
            date_from=stopped.date_from,
            date_till=stopped.date_till,
            requested=list(stopped.requested),
            order=list(stopped.order or stopped.requested),
            closed=list(stopped.closed),
            failed=list(stopped.failed),
            outcomes=dict(stopped.outcomes),
            skips=list(stopped.skips),
            sources=dict(stopped.sources),
            omitted=set(stopped.omitted),
            log=list(stopped.log),
            current=stopped.current or pending[0],
            run_id=stopped.run_id,
            started_at=stopped.started_at or dt.datetime.now(dt.UTC),
        )
        self._state.note_event(f"Прогон продолжен · осталось сессий {len(pending)}")

        self._task = asyncio.create_task(
            self._run(selected, sessions=pending), name="market-data-catchup"
        )
        return self._state.snapshot()

    def stop(self) -> dict[str, object]:
        """Попросить остановиться. Текущая сессия доводится до конца."""
        if not self.is_active:
            return self._state.snapshot()

        self._stop_requested = True
        self._state.stop_requested = True
        self._state.status = CatchupStatus.STOPPING
        # Остановка — событие прогона наравне с прочими: без неё человек видит,
        # что сбор встал, и не знает, сам он это сделал или что-то сломалось.
        self._state.note_event("Запрошена остановка · идущий источник доводится до конца")
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

            # Ручной выбор ограничивает и даты, и источники. Каждая выбранная
            # группа вносит только свои собственные окна; обязательность для
            # ML не должна протаскивать несвязанные старые дыры в этот план.
            missing: set[dt.date] = set()
            selected_historical = [group for group in selected if group.has_history]
            for group in selected_historical:
                depth = group.window_sessions(self._settings)
                if depth is None:
                    continue
                window = await calendar.window(asof, depth)
                group_missing = set(await completeness.missing_sessions(repository, group, window))
                audit: set[dt.date] = set()
                for source_id in group.source_ids:
                    audit.update(
                        await completeness.requires_audit_sessions(
                            repository, group, source_id, window
                        )
                    )
                missing.update(group_missing - audit)

        sessions, clamped = _clamp(sorted(missing), date_from, date_till)
        if not sessions:
            raise NothingToCatchUpError("пропущенных сессий нет")

        logger.info(
            "догон: к сбору %d сессий, группы %s",
            len(sessions),
            ", ".join(group.group_id.value for group in selected),
        )
        return sessions, clamped

    async def _run(
        self,
        selected: tuple[groups.SourceGroup, ...],
        sessions: list[dt.date] | None = None,
    ) -> None:
        """Тело фоновой задачи.

        ``sessions`` задаётся продолжением: собирать надо непройденное, а
        счётчик прогона продолжает считать по всему его плану (FR-058b).
        """
        plan_sessions = sessions if sessions is not None else self._state.requested

        if self._state.mode == plan_module.MODE_DAILY:
            await self._run_daily(plan_sessions)
            return

        factory = get_session_factory()
        try:
            async with factory() as session:
                result = await ingest.catch_up(
                    session,
                    self._settings,
                    plan_sessions[-1],
                    sessions=plan_sessions,
                    source_ids=groups.source_ids_for(selected),
                    on_session_start=self._on_session_start,
                    on_session_done=self._on_session_done,
                    on_source=self._on_source,
                    on_skip=self._state.note_skip,
                    should_stop=lambda: self._stop_requested,
                    run_id=self._state.run_id,
                )
        except Exception as error:
            self._state.status = CatchupStatus.FAILED
            self._state.reason = describe_failure(error)
            self._state.finished_at = dt.datetime.now(dt.UTC)
            logger.exception("догон завершился ошибкой")
            return

        # Списки собранного и несобранного ведёт `_on_session_done` по ходу
        # прогона. Переписывать их итогом нельзя: у продолжения в итоге только
        # его собственные сессии, а счёт идёт по всему прогону (FR-058b).
        # Последняя сессия ОСТАЁТСЯ названной. Прежде она обнулялась, и вместе
        # с ней с экрана пропадала вся лента источников: человек, остановивший
        # прогон, переставал видеть, на чём тот стоял, — при том что артефакт
        # подписывает эту ленту «Последняя сессия прогона» и показывает её у
        # законченного прогона наравне с идущим (FR-021, FR-025).
        self._state.finished_at = dt.datetime.now(dt.UTC)

        # Догон закрыл дыры — у ранжирования могла появиться работа. Ставится
        # ТОЛЬКО последняя готовая дата: исторические заданиями не становятся,
        # иначе один клик по догону породил бы сотни обращений к модели.
        await self._reconcile_daily_ml()
        self._state.status = (
            CatchupStatus.STOPPED if self._stop_requested else CatchupStatus.FINISHED
        )
        self._state.note_event(
            "Прогон остановлен по команде" if self._stop_requested else "Прогон завершён"
        )

        logger.info(
            "догон завершён: %s, закрыто %d из %d",
            self._state.status.value,
            len(result.closed),
            len(self._state.requested),
        )

    async def _reconcile_daily_ml(self) -> None:
        """Сообщить ранжированию, что данные могли стать готовы.

        Импорт локальный: сбор не должен зависеть от звена ранжирования — оно
        может отсутствовать, и это не мешает собирать.
        """
        from financial_ai.daily_ml import reconcile as daily_ml_reconcile

        try:
            factory = get_session_factory()
            async with factory() as session:
                await daily_ml_reconcile.reconcile(session, self._settings)
        except Exception:
            # Сбой ранжирования не отменяет собранное: данные уже записаны.
            logger.exception("реконсиляция ранжирования после догона не выполнена")

    async def _run_daily(self, plan_sessions: list[dt.date]) -> None:
        """Доделать остановленный ЕЖЕДНЕВНЫЙ прогон его же планом.

        Ручной догон собирает семь источников, ежедневный — десять, и гнать
        продолжение по ручному пути значило бы менять работу вместе с планом на
        экране (FR-058b).
        """
        factory = get_session_factory()
        try:
            async with factory() as session:
                for day in plan_sessions:
                    if self._stop_requested:
                        logger.info("продолжение остановлено перед сессией %s", day)
                        break

                    self._on_session_start(day)
                    result = await ingest.ingest_session(
                        session,
                        self._settings,
                        day,
                        on_source=self._on_source,
                        should_stop=lambda: self._stop_requested,
                        run_id=self._state.run_id,
                    )
                    self._on_session_done(
                        day, ingest.INTERRUPTED if result.interrupted else result.succeeded
                    )
        except Exception as error:
            self._state.status = CatchupStatus.FAILED
            self._state.reason = describe_failure(error)
            self._state.finished_at = dt.datetime.now(dt.UTC)
            logger.exception("продолжение завершилось ошибкой")
            return

        self._state.finished_at = dt.datetime.now(dt.UTC)
        self._state.status = (
            CatchupStatus.STOPPED if self._stop_requested else CatchupStatus.FINISHED
        )
        self._state.note_event(
            "Прогон остановлен по команде" if self._stop_requested else "Прогон завершён"
        )

    def _on_session_start(self, day: dt.date) -> None:
        self._state.begin_session(day)

    def _on_session_done(self, day: dt.date, closed: bool | str) -> None:
        """Исход сессии: собрана, не собрана либо прервана по команде.

        Прерванная — отдельно от несобранной: её план не доработан, и доделать
        его обязано продолжение. Несобранную доберёт обычный план по правилам
        повторов, и тащить её в продолжение значило бы перевыбирать её вечно
        (FR-058).
        """
        if closed == ingest.INTERRUPTED:
            self._state.failed.append(day)
            self._state.outcomes[day] = "partial"
        elif closed:
            self._state.closed.append(day)
            self._state.outcomes[day] = "collected"
        else:
            self._state.failed.append(day)
            self._state.outcomes[day] = "failed"

    def _on_source(self, source_id: str, status: str, outcome: object) -> None:
        """Ход по источникам внутри сессии.

        `running` приходит до обращения, исход — после. Так виден и текущий
        источник, и время последнего ответа: долгий источник перестаёт быть
        неотличимым от зависания.
        """
        state = RAIL_STATE.get(status, "skipped")
        detail = getattr(outcome, "shown", None) if outcome is not None else None
        self._state.note_source(source_id, state, detail)


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
