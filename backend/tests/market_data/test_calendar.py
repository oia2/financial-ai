"""Тесты торгового календаря.

Календарь — единственный источник истины о том, была ли сессия. От него зависит
и то, когда опрашивать биржу, и что считать «предыдущими N сессиями», и где в
данных настоящая дыра.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.repository import MarketDataRepository

pytestmark = pytest.mark.db

# Рабочая неделя плюс следующий понедельник. Выходные 29-30 августа пропущены.
WEEK = [
    dt.date(2026, 8, 24),
    dt.date(2026, 8, 25),
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
]


async def _seed(session: AsyncSession, dates: list[dt.date]) -> TradingCalendar:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(dates)
    await session.commit()
    return TradingCalendar(repository)


async def test_trading_day_is_recognised(db_session: AsyncSession) -> None:
    calendar = await _seed(db_session, WEEK)
    assert await calendar.is_session(dt.date(2026, 8, 28)) is True


async def test_weekend_is_not_a_session(db_session: AsyncSession) -> None:
    """Суббота между 28 и 31 августа торговой сессией не является."""
    calendar = await _seed(db_session, WEEK)
    assert await calendar.is_session(dt.date(2026, 8, 29)) is False


async def test_latest_session(db_session: AsyncSession) -> None:
    calendar = await _seed(db_session, WEEK)
    assert await calendar.latest_session() == dt.date(2026, 8, 31)


async def test_latest_session_not_after(db_session: AsyncSession) -> None:
    """В выходной последней завершённой остаётся пятничная сессия."""
    calendar = await _seed(db_session, WEEK)
    assert await calendar.latest_session(dt.date(2026, 8, 30)) == dt.date(2026, 8, 28)


async def test_window_counts_trading_sessions_not_calendar_days(db_session: AsyncSession) -> None:
    """Три сессии назад от понедельника — это среда, а не пятница.

    Отсчёт по календарным дням дал бы 28 августа; по торговым — 26-е.
    """
    calendar = await _seed(db_session, WEEK)
    window = await calendar.window(dt.date(2026, 8, 31), 3)
    assert window == [dt.date(2026, 8, 27), dt.date(2026, 8, 28), dt.date(2026, 8, 31)]
    assert window[0] != dt.date(2026, 8, 31) - dt.timedelta(days=2)


async def test_window_includes_asof_date(db_session: AsyncSession) -> None:
    calendar = await _seed(db_session, WEEK)
    window = await calendar.window(dt.date(2026, 8, 28), 1)
    assert window == [dt.date(2026, 8, 28)]


async def test_window_shorter_than_requested_is_not_an_error(db_session: AsyncSession) -> None:
    """На ранней истории окно короче: модель дополняет его слева сама."""
    calendar = await _seed(db_session, WEEK)
    window = await calendar.window(dt.date(2026, 8, 25), 314)
    assert window == [dt.date(2026, 8, 24), dt.date(2026, 8, 25)]


async def test_next_session_skips_weekend(db_session: AsyncSession) -> None:
    """`t+1` — следующая ТОРГОВАЯ сессия, а не завтрашний день."""
    calendar = await _seed(db_session, WEEK)
    assert await calendar.next_session(dt.date(2026, 8, 28)) == dt.date(2026, 8, 31)


async def test_next_session_absent_after_last(db_session: AsyncSession) -> None:
    calendar = await _seed(db_session, WEEK)
    assert await calendar.next_session(dt.date(2026, 8, 31)) is None


async def test_sessions_are_not_duplicated(db_session: AsyncSession) -> None:
    """Повторное добавление известных сессий ничего не меняет."""
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(WEEK)
    await db_session.commit()
    added = await repository.add_trading_sessions(WEEK)
    await db_session.commit()
    assert added == 0
