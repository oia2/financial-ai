"""Тесты догона пропущенных сессий.

Обращения к бирже и к ЦБ подменяются: сеть не нужна. База настоящая —
идемпотентность держится на ограничении уникальности, которого в подделке
не будет.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
from financial_ai.market_data.iss.client import IssError
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import equity_d1

pytestmark = pytest.mark.db

# Выходные между 28-м и 31-м намеренно не входят: календаря у них нет.
SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
    dt.date(2026, 9, 1),
]
ASOF = SESSIONS[-1]


def _quote(secid: str, day: dt.date) -> dict[str, object]:
    return {
        "SECID": secid,
        "TRADEDATE": day.isoformat(),
        "OPEN": "312.4",
        "HIGH": "315.1",
        "LOW": "311.0",
        "CLOSE": "314.22",
        "VOLUME": "1000",
    }


class FakeIss:
    """Подделка биржи. Считает обращения — это проверяемое поведение."""

    def __init__(self, fail_quotes_on: set[dt.date] | None = None) -> None:
        self.fail_quotes_on = fail_quotes_on or set()
        self.quote_calls: list[str] = []
        self.session_calls: list[str] = []
        self.history_calls: list[tuple[str, str, str]] = []

    async def fetch_security_history(
        self,
        secid: str,
        date_from: str,
        date_till: str,
        columns: tuple[str, ...],
        **kwargs: object,
    ) -> list[dict[str, object]]:
        self.history_calls.append((secid, date_from, date_till))
        return [{"TRADEDATE": d.isoformat(), "CLOSE": "3200.5"} for d in SESSIONS]

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.session_calls.append(session_date)
        if "OPEN" not in columns:
            return []
        self.quote_calls.append(session_date)
        day = dt.date.fromisoformat(session_date)
        if day in self.fail_quotes_on:
            raise IssError("биржа недоступна")
        return [_quote("SBER", day)]

    async def fetch_session_rows_for(
        self, session_date: str, columns: tuple[str, ...], **kwargs: object
    ) -> list[dict[str, object]]:
        self.session_calls.append(session_date)
        return []


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


# --- закрытие дыры (FR-005, SC-001) -----------------------------------------


async def test_catchup_closes_the_gap(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])
    iss = FakeIss()

    result = await ingest.catch_up(db_session, settings, ASOF, iss, cbr_client)

    assert result.requested == [SESSIONS[2], SESSIONS[3]]
    assert result.closed == [SESSIONS[2], SESSIONS[3]]
    assert result.failed == []

    repository = MarketDataRepository(db_session)
    assert await repository.sessions_with_daily_bars(SESSIONS) == set(SESSIONS)


async def test_catchup_goes_from_old_to_new(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """При прерывании остаётся закрытым ранний участок, а не разрозненные даты."""
    await _seed(db_session, [SESSIONS[4]])
    iss = FakeIss()

    await ingest.catch_up(db_session, settings, ASOF, iss, cbr_client)

    assert iss.quote_calls == [day.isoformat() for day in SESSIONS[:4]]


# --- границы окна (FR-003) ---------------------------------------------------


async def test_sessions_older_than_window_are_not_requested(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    narrow = Settings(market_data_catchup_window_sessions=2)
    iss = FakeIss()

    result = await ingest.catch_up(db_session, narrow, ASOF, iss, cbr_client)

    assert result.requested == [SESSIONS[3]]
    assert SESSIONS[0].isoformat() not in iss.quote_calls


# --- идемпотентность (FR-009, SC-002) ----------------------------------------


async def test_repeated_catchup_touches_nothing(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, SESSIONS)
    iss = FakeIss()

    result = await ingest.catch_up(db_session, settings, ASOF, iss, cbr_client)

    assert result.requested == []
    assert iss.quote_calls == []
    assert iss.session_calls == []


async def test_second_catchup_does_not_duplicate(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[4]])
    repository = MarketDataRepository(db_session)

    await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)
    first = len(await repository.daily_bars_for_window(SESSIONS))

    await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)
    second = len(await repository.daily_bars_for_window(SESSIONS))

    assert first == second


# --- запрет передатирования (FR-008, SC-004) ---------------------------------


async def test_observation_keeps_its_own_session(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Наблюдение за 28 августа остаётся наблюдением за 28 августа."""
    await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])

    await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)

    repository = MarketDataRepository(db_session)
    bars = await repository.daily_bars_for_window([SESSIONS[2]])
    assert [bar.session_date for bar in bars] == [SESSIONS[2]]


async def test_late_data_does_not_migrate_to_the_next_session(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])

    await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)

    repository = MarketDataRepository(db_session)
    bars = await repository.daily_bars_for_window(SESSIONS)
    stored = sorted({bar.session_date for bar in bars})
    assert stored == SESSIONS


# --- прерываемость (FR-010) --------------------------------------------------


async def test_interrupted_catchup_resumes(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Первый прогон закрывает часть, второй продолжает с оставшихся."""
    await _seed(db_session, [SESSIONS[4]])

    first = await ingest.catch_up(
        db_session, settings, ASOF, FakeIss(fail_quotes_on={SESSIONS[2], SESSIONS[3]}), cbr_client
    )
    assert first.closed == [SESSIONS[0], SESSIONS[1]]
    assert first.failed == [SESSIONS[2], SESSIONS[3]]

    iss = FakeIss()
    second = await ingest.catch_up(db_session, settings, ASOF, iss, cbr_client)

    assert second.requested == [SESSIONS[2], SESSIONS[3]]
    assert iss.quote_calls == [SESSIONS[2].isoformat(), SESSIONS[3].isoformat()]


# --- устойчивость (FR-013) ---------------------------------------------------


async def test_one_failed_session_does_not_stop_the_rest(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    iss = FakeIss(fail_quotes_on={SESSIONS[1]})

    result = await ingest.catch_up(db_session, settings, ASOF, iss, cbr_client)

    assert result.failed == [SESSIONS[1]]
    assert result.closed == [SESSIONS[0], SESSIONS[2], SESSIONS[3]]


async def test_failed_session_does_not_lose_collected_data(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[4]])
    repository = MarketDataRepository(db_session)

    await ingest.catch_up(
        db_session, settings, ASOF, FakeIss(fail_quotes_on={SESSIONS[1]}), cbr_client
    )

    stored = await repository.sessions_with_daily_bars(SESSIONS)
    assert SESSIONS[0] in stored
    assert SESSIONS[4] in stored


# --- признак прогона (FR-017) ------------------------------------------------


async def test_catchup_runs_are_marked(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[1], SESSIONS[4]])

    await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)

    repository = MarketDataRepository(db_session)
    runs = await repository.runs_for_session(SESSIONS[2])
    quotes = [run for run in runs if run.source_id == equity_d1.SOURCE_ID]
    assert quotes and all(run.trigger == "catchup" for run in quotes)


async def test_daily_run_is_marked_daily(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, SESSIONS)

    await ingest.ingest_session(db_session, settings, SESSIONS[2], FakeIss(), cbr_client)

    repository = MarketDataRepository(db_session)
    runs = await repository.runs_for_session(SESSIONS[2])
    quotes = [run for run in runs if run.source_id == equity_d1.SOURCE_ID]
    assert quotes and all(run.trigger == "daily" for run in quotes)


# --- разграничение с первичной загрузкой (FR-014, FR-015, SC-006) ------------


async def test_empty_storage_makes_no_exchange_calls(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [])
    iss = FakeIss()

    result = await ingest.catch_up(db_session, settings, ASOF, iss, cbr_client)

    assert result.needs_backfill is True
    assert result.requested == []
    assert iss.quote_calls == []
    assert iss.session_calls == []
    assert result.skipped_reason is not None


async def test_backfilled_storage_catches_up_normally(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await _seed(db_session, [SESSIONS[0]])

    result = await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)

    assert result.needs_backfill is False
    assert result.requested == SESSIONS[1:]


# --- отсутствие порога (FR-016) ----------------------------------------------


async def test_long_gap_is_caught_up_entirely(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Порога по числу пропущенных сессий нет: догоняется всё окно."""
    await _seed(db_session, [SESSIONS[0]])

    result = await ingest.catch_up(db_session, settings, ASOF, FakeIss(), cbr_client)

    assert len(result.closed) == 4


# --- выключение (настройка) --------------------------------------------------


async def test_disabled_catchup_does_nothing(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient
) -> None:
    """Выключение оставляет обнаружение: знать о дыре полезно и без починки."""
    await _seed(db_session, [SESSIONS[0]])
    off = Settings(market_data_catchup_enabled=False, market_data_catchup_window_sessions=5)
    iss = FakeIss()

    result = await ingest.catch_up(db_session, off, ASOF, iss, cbr_client)

    assert result.requested == []
    assert iss.quote_calls == []
