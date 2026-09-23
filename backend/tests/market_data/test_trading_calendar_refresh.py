from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data.sources import trading_calendar


class FakeRepository:
    def __init__(self, latest: dt.date | None) -> None:
        self.latest = latest
        self.saved: list[dt.date] = []

    async def latest_trading_session(self) -> dt.date | None:
        return self.latest

    async def add_trading_sessions(self, dates: list[dt.date]) -> int:
        self.saved = dates
        return len(dates)


class FakeClient:
    def __init__(self) -> None:
        self.bounds: tuple[str, str] | None = None

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, str]]:
        self.bounds = (date_from, date_till)
        return []


@pytest.mark.asyncio
async def test_calendar_daily_refresh_overlaps_saved_tail_and_catches_up() -> None:
    client = FakeClient()
    repository = FakeRepository(dt.date(2026, 1, 5))

    await trading_calendar.sync_trading_calendar(
        client, repository, "SBER", date_till=dt.date(2026, 9, 20)
    )

    assert client.bounds == ("2025-12-22", "2026-09-20")


@pytest.mark.asyncio
async def test_calendar_initial_and_explicit_ranges_remain_full() -> None:
    client = FakeClient()
    repository = FakeRepository(None)
    await trading_calendar.sync_trading_calendar(
        client, repository, "SBER", date_till=dt.date(2026, 9, 20)
    )
    assert client.bounds == ("1990-01-01", "2026-09-20")

    await trading_calendar.sync_trading_calendar(
        client,
        FakeRepository(dt.date(2026, 9, 19)),
        "SBER",
        date_from=dt.date(2026, 1, 1),
        date_till=dt.date(2026, 2, 1),
    )
    assert client.bounds == ("2026-01-01", "2026-02-01")


@pytest.mark.asyncio
async def test_calendar_upper_bound_is_the_moscow_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """В контейнере UTC: с 00:00 до 03:00 МСК граница отставала на сутки (FR-040a)."""
    monkeypatch.setattr(trading_calendar, "moscow_today", lambda: dt.date(2026, 9, 24))
    client = FakeClient()

    await trading_calendar.sync_trading_calendar(
        client, FakeRepository(dt.date(2026, 9, 22)), "SBER"
    )

    assert client.bounds == ("2026-09-08", "2026-09-24")
