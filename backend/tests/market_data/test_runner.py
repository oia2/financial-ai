"""Тесты управляемого догона.

Догон стал фоновой задачей: состояние живёт в процессе, остановка мягкая,
второй запуск отклоняется. Сбор здесь подменяется — проверяется управление, а
не источники.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.runner import (
    BackfillRequiredError,
    CatchupAlreadyRunningError,
    CatchupRunner,
    CatchupStatus,
    NothingToCatchUpError,
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
    await session.commit()


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
