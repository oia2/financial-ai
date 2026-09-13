"""Доведение данных до последней закрытой сессии (US2, FR-006–FR-010).

Догон закрывает дыры внутри окна, но границу окна не двигает: новых сессий он
не видит, пока календарь не расширен. Эта проверка — про границу.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import advance
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 9, 7),
    dt.date(2026, 9, 8),
    dt.date(2026, 9, 9),
    dt.date(2026, 9, 10),
    dt.date(2026, 9, 11),
]

# Вечер пятницы 11.09 после времени сбора: все пять сессий закрыты.
FRIDAY_EVENING = dt.datetime(2026, 9, 11, 20, 0)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_catchup_window_sessions=len(SESSIONS),
        market_data_startup_recovery_max_sessions=3,
    )


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("300"),
        high=Decimal("301"),
        low=Decimal("299"),
        close=Decimal("300.5"),
        volume=Decimal("1000"),
    )


async def _seed(session: AsyncSession, collected: list[dt.date]) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSIONS[-1])
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSIONS[-1])
    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])
    await session.commit()
    return repository


# --- закрытость сессии (FR-007) ----------------------------------------------


def test_past_session_is_closed(settings: Settings) -> None:
    assert advance.session_is_closed(dt.date(2026, 9, 10), settings, FRIDAY_EVENING)


def test_today_before_close_is_not_closed(settings: Settings) -> None:
    """Незавершённая сессия не собирается: это утечка будущего в признаки."""
    morning = dt.datetime(2026, 9, 11, 12, 0)

    assert not advance.session_is_closed(dt.date(2026, 9, 11), settings, morning)


def test_today_after_close_is_closed(settings: Settings) -> None:
    assert advance.session_is_closed(dt.date(2026, 9, 11), settings, FRIDAY_EVENING)


def test_future_session_is_never_closed(settings: Settings) -> None:
    assert not advance.session_is_closed(dt.date(2026, 9, 14), settings, FRIDAY_EVENING)


def test_custom_close_time_is_respected() -> None:
    """Время сбора — настройка, и его сдвиг сдвигает границу закрытости."""
    late = Settings(market_data_ingest_after_close="21:00")
    today = dt.date(2026, 9, 11)

    assert not advance.session_is_closed(today, late, dt.datetime(2026, 9, 11, 20, 0))
    assert advance.session_is_closed(today, late, dt.datetime(2026, 9, 11, 21, 0))


def test_malformed_close_time_falls_back_without_crashing() -> None:
    """Опечатка в настройке не должна останавливать сбор навсегда."""
    broken = Settings(market_data_ingest_after_close="не время")
    today = dt.date(2026, 9, 11)

    assert advance.session_is_closed(today, broken, dt.datetime(2026, 9, 11, 19, 30))
    assert not advance.session_is_closed(today, broken, dt.datetime(2026, 9, 11, 12, 0))


# --- отставание (FR-006, FR-008, FR-009) --------------------------------------


async def test_pending_sessions_are_those_after_the_last_collected(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session, collected=SESSIONS[:2])

    pending, last_closed = await advance.pending_sessions(db_session, settings, FRIDAY_EVENING)

    assert pending == SESSIONS[2:]
    assert last_closed == SESSIONS[-1]


async def test_nothing_pending_when_everything_collected(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session, collected=SESSIONS)

    pending, last_closed = await advance.pending_sessions(db_session, settings, FRIDAY_EVENING)

    assert pending == []
    assert last_closed == SESSIONS[-1]


async def test_unclosed_session_is_not_pending(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Сегодняшняя сессия до закрытия в работу не берётся."""
    await _seed(db_session, collected=SESSIONS[:-1])
    morning = dt.datetime(2026, 9, 11, 12, 0)

    pending, last_closed = await advance.pending_sessions(db_session, settings, morning)

    assert pending == []
    assert last_closed == SESSIONS[-2]


async def test_large_gap_is_not_collected_when_limit_is_set(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заданный числом предел останавливает автоматический сбор целиком.

    Не «собрать три из четырёх»: нарезка — тот же неуправляемый догон, только
    растянутый во времени. Предел перестал быть умолчанием, но рычаг остался:
    неуправляемый догон однажды ушёл на 2909 обращений к бирже без спроса.
    """
    await _seed(db_session, collected=SESSIONS[:1])

    called: list[dt.date] = []

    async def never(*args: object, **kwargs: object) -> object:  # pragma: no cover
        called.append(dt.date.today())
        raise AssertionError("сбор не должен выполняться при разрыве сверх предела")

    monkeypatch.setattr(advance.ingest, "ingest_session", never)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, settings, FRIDAY_EVENING)

    assert result.limit_exceeded
    assert result.gap_sessions == 4
    assert result.collected == []
    assert called == []


async def test_default_collects_the_whole_gap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без заданного предела разрыв догоняется целиком, в границах окна догона.

    Ровно тот же разрыв в четыре сессии, что останавливается заданным пределом
    в тесте выше. Умолчание `0` означает «без предела»: граница и так задана
    окном догона, глубже него сессия до модели не доходит.
    """
    unlimited = Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_catchup_window_sessions=len(SESSIONS),
        market_data_startup_recovery_max_sessions=0,
    )
    await _seed(db_session, collected=SESSIONS[:1])

    collected: list[dt.date] = []

    async def collect(_session: object, _settings: object, day: dt.date) -> object:
        collected.append(day)
        return SimpleNamespace(succeeded=True, unfinished_sources=[])

    monkeypatch.setattr(advance.ingest, "ingest_session", collect)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, unlimited, FRIDAY_EVENING)

    assert not result.limit_exceeded
    assert collected == SESSIONS[1:]
    assert result.collected == SESSIONS[1:]
    assert result.pending == []
    assert result.gap_sessions == 0


async def _noop(*args: object, **kwargs: object) -> int:
    return 0


class _FakeClient:
    """Клиент биржи, который никуда не ходит."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None
