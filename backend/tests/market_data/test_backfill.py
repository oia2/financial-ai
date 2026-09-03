"""Тесты первичной загрузки истории.

Главное свойство — возобновляемость: прерванная на третьем часу загрузка не
должна означать три потерянных часа.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import backfill
from financial_ai.market_data.iss.client import IssError
from financial_ai.market_data.repository import MarketDataRepository

pytestmark = pytest.mark.db

DAYS = [dt.date(2026, 8, 26), dt.date(2026, 8, 27), dt.date(2026, 8, 28)]


class FakeIss:
    """Подделка биржи. Считает обращения по бумагам — это проверяемое поведение."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.security_calls: list[str] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.security_calls.append(secid)
        if secid in self.fail_for:
            raise IssError("биржа недоступна")
        return [
            {
                "SECID": secid,
                "TRADEDATE": day.isoformat(),
                "OPEN": "312.4",
                "HIGH": "315.1",
                "LOW": "311.0",
                "CLOSE": "314.22",
                "VOLUME": "1000",
            }
            for day in DAYS
        ]


@pytest.fixture
def settings() -> Settings:
    return Settings()


# --- глубина -----------------------------------------------------------------


def test_empty_setting_means_all_available_history() -> None:
    """Пустая настройка — вся история: докачка потом обойдётся дороже."""
    assert (
        backfill.resolve_start_date(Settings(market_data_backfill_from=""))
        == backfill.EARLIEST_DATE
    )


def test_configured_date_is_used() -> None:
    settings = Settings(market_data_backfill_from="2025-01-01")
    assert backfill.resolve_start_date(settings) == dt.date(2025, 1, 1)


def test_malformed_date_falls_back_to_full_history() -> None:
    """Опечатка не должна тихо обрезать историю до нуля."""
    settings = Settings(market_data_backfill_from="первое января")
    assert backfill.resolve_start_date(settings) == backfill.EARLIEST_DATE


# --- загрузка ----------------------------------------------------------------


async def test_history_is_loaded(db_session: AsyncSession, settings: Settings) -> None:
    iss = FakeIss()
    await backfill.backfill_equity(db_session, settings, iss, ["SBER", "GAZP"])

    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(DAYS[-1]) == 2
    bars = await repository.daily_bars_for_window(DAYS)
    assert len(bars) == 6
    assert bars[0].close == Decimal("314.22")


async def test_backfill_walks_securities_not_dates(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Первичная загрузка ходит по бумагам — за всю историю сразу."""
    iss = FakeIss()
    await backfill.backfill_equity(db_session, settings, iss, ["SBER", "GAZP"])
    assert iss.security_calls == ["SBER", "GAZP"]


# --- возобновляемость --------------------------------------------------------


async def test_completed_tickers_are_skipped(db_session: AsyncSession, settings: Settings) -> None:
    """FR-009: повторный запуск продолжает, а не начинает заново."""
    first = FakeIss()
    await backfill.backfill_equity(db_session, settings, first, ["SBER", "GAZP"])

    second = FakeIss()
    await backfill.backfill_equity(db_session, settings, second, ["SBER", "GAZP", "LKOH"])

    assert second.security_calls == ["LKOH"]


async def test_progress_reports_remaining(db_session: AsyncSession, settings: Settings) -> None:
    await backfill.backfill_equity(db_session, settings, FakeIss(), ["SBER"])
    progress = await backfill.backfill_equity(
        db_session, settings, FakeIss(), ["SBER", "GAZP", "LKOH"]
    )
    assert progress.total == 3
    assert "SBER" in progress.completed


async def test_interruption_keeps_loaded_data(db_session: AsyncSession, settings: Settings) -> None:
    """Одна недоступная бумага не отменяет уже загруженные."""
    iss = FakeIss(fail_for={"GAZP"})
    await backfill.backfill_equity(db_session, settings, iss, ["SBER", "GAZP", "LKOH"])

    repository = MarketDataRepository(db_session)
    tickers = await repository.tickers_with_history()
    assert tickers == {"SBER", "LKOH"}


async def test_failed_ticker_is_retried_next_run(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Не загрузившаяся бумага должна попасть в следующий прогон."""
    await backfill.backfill_equity(
        db_session, settings, FakeIss(fail_for={"GAZP"}), ["SBER", "GAZP"]
    )
    second = FakeIss()
    await backfill.backfill_equity(db_session, settings, second, ["SBER", "GAZP"])
    assert second.security_calls == ["GAZP"]


async def test_calendar_is_filled_first(db_session: AsyncSession, settings: Settings) -> None:
    """Пока неизвестно, какие дни были торговыми, остальное не имеет смысла."""
    added = await backfill.backfill_calendar(db_session, settings, FakeIss())
    assert added == len(DAYS)

    repository = MarketDataRepository(db_session)
    assert await repository.is_trading_session(DAYS[0]) is True
