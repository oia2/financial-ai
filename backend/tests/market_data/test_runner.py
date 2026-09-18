"""Тесты управляемого догона.

Догон стал фоновой задачей: состояние живёт в процессе, остановка мягкая,
второй запуск отклоняется. Сбор здесь подменяется — проверяется управление, а
не источники.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
from financial_ai.market_data.iss.client import IssError
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.runner import (
    BackfillRequiredError,
    CatchupAlreadyRunningError,
    CatchupRunner,
    CatchupStatus,
    NothingToCatchUpError,
    describe_failure,
)

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
    dt.date(2026, 9, 1),
]
ASOF = SESSIONS[-1]


@pytest.fixture
def settings() -> Settings:
    return Settings(market_data_catchup_window_sessions=5)


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("312.4"),
        high=Decimal("315.1"),
        low=Decimal("311.0"),
        close=Decimal("314.22"),
        volume=Decimal("1000"),
    )


async def _seed(session: AsyncSession, collected: list[dt.date]) -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])
        await _mark_collected(repository, collected)
    await session.commit()


async def _mark_collected(repository: MarketDataRepository, days: list[dt.date]) -> None:
    """Отметить сессии собранными ПО ВСЕМ группам.

    Котировок мало: сессия закрыта, когда закрыт каждый источник каждой группы.
    Прежде план сбора считался по одним котировкам, и бар делал сессию
    «собранной» — из-за этого ручной догон брал в работу вчетверо меньше, чем
    было недобрано (FR-031, FR-032).
    """
    from financial_ai.market_data import groups as group_registry

    moment = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    for day in days:
        for group in group_registry.GROUPS:
            for source_id in group.source_ids:
                await repository.record_run(
                    run_id=f"seed-{day}-{source_id}",
                    source_id=source_id,
                    status="ok",
                    started_at=moment,
                    finished_at=moment,
                    session_date=day,
                    rows_written=1,
                )


class FakeCatchUp:
    """Подделка сбора: считает сессии и слушается остановки."""

    def __init__(self, delay: float = 0.0, fail_on: set[dt.date] | None = None) -> None:
        self.delay = delay
        self.fail_on = fail_on or set()
        self.visited: list[dt.date] = []
        self.source_ids: frozenset[str] | None = None

    async def __call__(self, session, settings, asof_date, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = ingest.CatchupResult()
        requested = list(kwargs.get("sessions") or [])
        result.requested = requested
        self.source_ids = kwargs.get("source_ids")
        should_stop = kwargs.get("should_stop")
        on_start = kwargs.get("on_session_start")
        on_done = kwargs.get("on_session_done")

        for day in requested:
            if should_stop is not None and should_stop():
                break
            if on_start is not None:
                on_start(day)
            if self.delay:
                await asyncio.sleep(self.delay)
            self.visited.append(day)
            closed = day not in self.fail_on
            if closed:
                result.closed.append(day)
            else:
                result.failed.append(day)
            if on_done is not None:
                on_done(day, closed)
        return result


async def _wait_until_idle(instance: CatchupRunner, tries: int = 200) -> None:
    for _ in range(tries):
        if not instance.is_active:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("догон не завершился")


# --- состояние (data-model.md §4) --------------------------------------------


async def test_idle_before_any_start(db_session: AsyncSession, settings: Settings) -> None:
    instance = CatchupRunner(settings)
    assert instance.status()["status"] == CatchupStatus.IDLE.value


async def test_running_then_finished(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    fake = FakeCatchUp()
    monkeypatch.setattr(ingest, "catch_up", fake)

    instance = CatchupRunner(settings)
    started = await instance.start()
    assert started["status"] == CatchupStatus.RUNNING.value

    await _wait_until_idle(instance)
    assert instance.status()["status"] == CatchupStatus.FINISHED.value
    assert fake.visited == SESSIONS[:4]


async def test_failure_gives_failed_status(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, [SESSIONS[4]])

    async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("биржа лежит")

    monkeypatch.setattr(ingest, "catch_up", boom)

    instance = CatchupRunner(settings)
    await instance.start()
    await _wait_until_idle(instance)

    state = instance.status()
    assert state["status"] == CatchupStatus.FAILED.value
    assert state["reason"] is not None


async def test_failure_reason_is_fit_to_be_shown(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Причина уходит на экран, поэтому текста исключения в ней нет.

    `repr` ошибки `httpx` несёт адрес обращения, а он может быть внутренним:
    показывать конфигурацию развёртывания пользователю нельзя (FR-005a фичи
    005, FR-043 фичи 006). Подробности остаются в журнале.
    """
    await _seed(db_session, [SESSIONS[4]])

    async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("[Errno 111] Connection refused to http://backend-worker:8000")

    monkeypatch.setattr(ingest, "catch_up", boom)

    instance = CatchupRunner(settings)
    await instance.start()
    await _wait_until_idle(instance)

    reason = instance.status()["reason"]
    assert isinstance(reason, str)
    assert reason == "источник данных недоступен"
    assert "backend-worker" not in reason
    assert "Errno" not in reason


def test_every_failure_class_has_its_own_wording() -> None:
    """Разные причины различимы: «недоступен» и «не ответил» — не одно и то же."""
    assert describe_failure(httpx.ConnectError("x")) == "источник данных недоступен"
    assert describe_failure(httpx.ReadTimeout("x")) == "источник данных не ответил вовремя"
    assert describe_failure(IssError("x")) == "источник данных ответил ошибкой"
    assert describe_failure(RuntimeError("x")) == "прогон прерван внутренней ошибкой"


# --- мягкая остановка (FR-006, SC-002) ---------------------------------------


async def test_stop_finishes_the_current_session(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Начатая сессия доводится до конца: половина дня хуже, чем ничего."""
    await _seed(db_session, [SESSIONS[4]])
    fake = FakeCatchUp(delay=0.05)
    monkeypatch.setattr(ingest, "catch_up", fake)

    instance = CatchupRunner(settings)
    await instance.start()
    await asyncio.sleep(0.07)

    stopped = instance.stop()
    assert stopped["status"] == CatchupStatus.STOPPING.value

    await _wait_until_idle(instance)
    assert instance.status()["status"] == CatchupStatus.STOPPED.value
    assert len(fake.visited) < 4


async def test_stop_when_nothing_runs_is_not_an_error(
    db_session: AsyncSession, settings: Settings
) -> None:
    instance = CatchupRunner(settings)
    assert instance.stop()["status"] == CatchupStatus.IDLE.value


# --- продолжение (FR-007, SC-003) --------------------------------------------


async def test_second_start_continues_from_the_rest(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уже закрытые сессии заново не собираются: план строится по данным."""
    await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])
    fake = FakeCatchUp()
    monkeypatch.setattr(ingest, "catch_up", fake)

    instance = CatchupRunner(settings)
    await instance.start()
    await _wait_until_idle(instance)

    assert fake.visited == [SESSIONS[2], SESSIONS[3]]


# --- единственность (FR-008) --------------------------------------------------


async def test_second_start_while_running_is_rejected(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два одновременных догона писали бы одно и то же и удваивали обращения."""
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp(delay=0.05))

    instance = CatchupRunner(settings)
    await instance.start()

    with pytest.raises(CatchupAlreadyRunningError):
        await instance.start()

    await _wait_until_idle(instance)


# --- выбор групп и диапазона (FR-003, FR-004) --------------------------------


async def test_only_selected_groups_are_collected(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    fake = FakeCatchUp()
    monkeypatch.setattr(ingest, "catch_up", fake)

    instance = CatchupRunner(settings)
    await instance.start(group_ids=["quotes"])
    await _wait_until_idle(instance)

    assert fake.source_ids == frozenset({"equity_d1"})


async def test_range_narrows_the_plan(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    fake = FakeCatchUp()
    monkeypatch.setattr(ingest, "catch_up", fake)

    instance = CatchupRunner(settings)
    state = await instance.start(date_from=SESSIONS[1], date_till=SESSIONS[2])
    await _wait_until_idle(instance)

    assert state["clamped"] is False
    assert fake.visited == [SESSIONS[1], SESSIONS[2]]


async def test_range_wider_than_window_is_clamped_and_visible(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обрезка видна: человек должен знать, что его диапазон сузили."""
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp())

    instance = CatchupRunner(settings)
    state = await instance.start(date_from=dt.date(2020, 1, 1), date_till=dt.date(2030, 1, 1))
    await _wait_until_idle(instance)

    assert state["clamped"] is True


async def test_unknown_group_is_rejected(db_session: AsyncSession, settings: Settings) -> None:
    from financial_ai.market_data.groups import UnknownGroupError

    await _seed(db_session, [SESSIONS[4]])
    instance = CatchupRunner(settings)

    with pytest.raises(UnknownGroupError):
        await instance.start(group_ids=["котировки"])


# --- разграничение с первичной загрузкой -------------------------------------


async def test_empty_storage_requires_backfill(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session, [])
    instance = CatchupRunner(settings)

    with pytest.raises(BackfillRequiredError):
        await instance.start()


async def test_full_window_has_nothing_to_catch_up(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session, SESSIONS)
    instance = CatchupRunner(settings)

    with pytest.raises(NothingToCatchUpError):
        await instance.start()


@pytest.mark.db
async def test_stop_is_written_to_the_event_log(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Остановка — событие прогона (T093).

    Без неё человек видит, что сбор встал, и не знает, сам он это сделал или
    что-то сломалось.
    """
    await _seed(db_session, [SESSIONS[4]])
    monkeypatch.setattr(ingest, "catch_up", FakeCatchUp(delay=0.05))
    instance = CatchupRunner(settings)

    await instance.start()
    instance.stop()
    await _wait_until_idle(instance)

    texts = [event["text"] for event in instance.status()["log"]]  # type: ignore[index]
    assert any("Запрошена остановка" in text for text in texts)
    assert any("остановлен по команде" in text for text in texts)


# --- состояние диапазонных источников (FR-057) -------------------------------


def test_диапазонный_источник_переживает_границу_сессии() -> None:
    """Он идёт ОДИН раз на прогон — перед циклом сессий.

    План источников начинается заново на каждой сессии, и вместе с
    посессионными стирались диапазонные: «Глобальные ряды» и «Курсы и ставка
    ЦБ» оставались ожидающими до конца ручного прогона — тем же способом,
    каким до FR-056 висел ожидающим торговый календарь.
    """
    from financial_ai.market_data import plan as plan_module
    from financial_ai.market_data.runner import CatchupState

    state = CatchupState(mode=plan_module.MODE_MANUAL, requested=list(SESSIONS))
    state.begin_session(SESSIONS[0])
    state.note_source("global_series", "done")
    state.note_source("equity_d1", "done")

    state.begin_session(SESSIONS[1])

    plan_rows = {row["source_id"]: row["state"] for row in state.snapshot()["current"]["sources"]}  # type: ignore[index]
    assert plan_rows["global_series"] == "done"
    assert plan_rows["equity_d1"] == "pending"


def test_посессионный_источник_границу_сессии_не_переживает() -> None:
    """Обратная форма: план сессии обязан начинаться заново, иначе счёт врёт."""
    from financial_ai.market_data import plan as plan_module
    from financial_ai.market_data.runner import CatchupState

    state = CatchupState(mode=plan_module.MODE_MANUAL, requested=list(SESSIONS))
    state.begin_session(SESSIONS[0])
    for spec in plan_module.for_mode(plan_module.MODE_MANUAL):
        state.note_source(spec.source_id, "done")

    state.begin_session(SESSIONS[1])

    rows = state.snapshot()["current"]["sources"]  # type: ignore[index]
    per_session = [row for row in rows if row["scope"] == plan_module.SESSION]
    assert per_session and all(row["state"] == "pending" for row in per_session)


# --- непройденные сессии (FR-058) --------------------------------------------


def test_непройденными_считаются_сессии_без_исхода() -> None:
    """Продолжению нужно ровно это: что осталось доделать."""
    from financial_ai.market_data.runner import CatchupState

    state = CatchupState(requested=list(SESSIONS))
    state.outcomes[SESSIONS[0]] = "collected"
    state.outcomes[SESSIONS[1]] = "failed"
    state.outcomes[SESSIONS[2]] = "skipped"

    assert state.unfinished == SESSIONS[3:]


def test_пройденный_целиком_прогон_продолжать_нечем() -> None:
    """Обратная форма: доведённый до конца прогон продолжения не предлагает."""
    from financial_ai.market_data.runner import CatchupState

    state = CatchupState(requested=list(SESSIONS))
    for day in SESSIONS:
        state.outcomes[day] = "collected"

    assert state.unfinished == []
