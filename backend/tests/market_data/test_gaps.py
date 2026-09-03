"""Тесты обнаружения пропущенных сессий.

Проверяется главное свойство: пропуск — это **разность**, а не хранимое
состояние. Всё, что модуль утверждает, он выводит из календаря и собранного, и
разойтись с фактом не может.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import gaps
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import equity_d1

pytestmark = pytest.mark.db

# Пять торговых сессий подряд; выходные между 28-м и 31-м намеренно пропущены —
# по календарю их не было.
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


async def _seed(session: AsyncSession, collected: list[dt.date]) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])
    await session.commit()
    return repository


async def test_missing_sessions_are_found(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])
    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert report.missing_sessions == [SESSIONS[2], SESSIONS[3]]
    assert report.has_gaps is True


async def test_full_window_has_no_gaps(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session, SESSIONS)
    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert report.missing_sessions == []
    assert report.has_gaps is False


async def test_non_trading_day_is_not_a_gap(db_session: AsyncSession, settings: Settings) -> None:
    """Между 28 и 31 августа выходные: календарь их не знает, пропуска нет."""
    await _seed(db_session, SESSIONS)
    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert dt.date(2026, 8, 29) not in report.window
    assert dt.date(2026, 8, 30) not in report.window


async def test_successful_run_without_rows_is_not_a_gap(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Биржа ответила, данных за день нет — сессия собрана, повторять незачем."""
    repository = await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id=equity_d1.SOURCE_ID,
        status="ok",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[2],
        rows_written=0,
    )
    await db_session.commit()

    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert SESSIONS[2] not in report.missing_sessions
    assert report.missing_sessions == [SESSIONS[3]]


async def test_failed_run_leaves_the_session_missing(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Неуспешный прогон сессию не закрывает: данных от него не осталось."""
    repository = await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id=equity_d1.SOURCE_ID,
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[2],
        failure_reason="источник не ответил",
    )
    await db_session.commit()

    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert SESSIONS[2] in report.missing_sessions


async def test_session_older_than_window_is_not_missing(db_session: AsyncSession) -> None:
    """Сессия старше окна в набор не попадёт, и догонять её незачем."""
    await _seed(db_session, [SESSIONS[3], SESSIONS[4]])
    narrow = Settings(market_data_catchup_window_sessions=2)
    report = await gaps.find_gaps(db_session, narrow, ASOF)
    assert report.window == SESSIONS[3:]
    assert report.missing_sessions == []


async def test_empty_storage_is_not_a_gap(db_session: AsyncSession, settings: Settings) -> None:
    """На чистой базе календарь полон, а наблюдений нет: это первичная загрузка."""
    await _seed(db_session, [])
    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert report.needs_backfill is True
    assert report.missing_sessions == []


async def test_window_defaults_to_price_window(db_session: AsyncSession) -> None:
    """Ноль означает «как окно цен»: предел выводится из устройства системы."""
    settings = Settings(
        market_data_catchup_window_sessions=0,
        market_data_price_window_sessions=3,
    )
    await _seed(db_session, SESSIONS)
    report = await gaps.find_gaps(db_session, settings, ASOF)
    assert len(report.window) == 3


async def test_incomplete_by_session_names_the_source(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Полнота объявляется по сессии И источнику, а не плоским списком дат."""
    repository = await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id="futures_positions",
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[0],
        failure_reason="источник не ответил",
    )
    await db_session.commit()

    report = await gaps.find_gaps(db_session, settings, ASOF)
    incomplete = report.incomplete_by_session()

    assert incomplete[SESSIONS[0]] == ["futures_positions"]
    assert incomplete[SESSIONS[2]] == [equity_d1.SOURCE_ID]


async def test_successful_retry_clears_the_unfinished_mark(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Иначе перечень пропусков превратился бы в журнал былых неудач.

    Источник упал, потом собрался со второй попытки — незакрытым он больше не
    считается.
    """
    repository = await _seed(db_session, SESSIONS)
    first = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    second = dt.datetime.now(dt.UTC)

    await repository.record_run(
        run_id="run-1",
        source_id="futures_positions",
        status="failed",
        started_at=first,
        finished_at=first,
        session_date=SESSIONS[1],
        failure_reason="источник не ответил",
    )
    await repository.record_run(
        run_id="run-2",
        source_id="futures_positions",
        status="ok",
        started_at=second,
        finished_at=second,
        session_date=SESSIONS[1],
        rows_written=42,
        trigger="catchup",
    )
    await db_session.commit()

    report = await gaps.find_gaps(db_session, settings, ASOF)

    assert report.unfinished == []


async def test_unfinished_source_is_listed_once(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Одна строка на пару «сессия — источник», а не на каждую попытку."""
    repository = await _seed(db_session, SESSIONS)
    for number in range(3):
        moment = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=number)
        await repository.record_run(
            run_id=f"run-{number}",
            source_id="futures_positions",
            status="failed",
            started_at=moment,
            finished_at=moment,
            session_date=SESSIONS[1],
            failure_reason="источник не ответил",
        )
    await db_session.commit()

    report = await gaps.find_gaps(db_session, settings, ASOF)

    assert len(report.unfinished) == 1
    assert report.unfinished[0].source_id == "futures_positions"
