"""Quotes and aggregates share one ISS board traversal."""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data.sources import equity_agg, equity_d1


class FakeIss:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch_session_rows(
        self, date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.calls += 1
        assert set(equity_d1.BOARD_COLUMNS) == set(columns)
        return [
            {
                "SECID": "SBER",
                "OPEN": "10",
                "HIGH": "11",
                "LOW": "9",
                "CLOSE": "10.5",
                "VOLUME": "2",
                "VALUE": "21",
                "NUMTRADES": "3",
                "WAPRICE": "10.4",
            }
        ]


class Repository:
    async def aliases_on(self, day: dt.date) -> dict[str, str]:
        return {}

    async def upsert_asset(self, *args: object) -> None:
        return None

    async def upsert_price_series(self, *args: object) -> None:
        return None

    async def upsert_daily_bars(self, rows: list[object]) -> int:
        return len(rows)

    async def upsert_aggregates(self, rows: list[object]) -> int:
        return len(rows)


@pytest.mark.asyncio
async def test_quotes_and_aggregates_use_one_board_fetch() -> None:
    client = FakeIss()
    repository = Repository()
    day = dt.date(2026, 9, 21)

    quotes = await equity_d1.sync_equity_daily(client, repository, day)  # type: ignore[arg-type]
    aggregates = await equity_agg.sync_equity_aggregates(client, repository, day)  # type: ignore[arg-type]

    assert client.calls == 1
    assert quotes.rows_written == aggregates.rows_written == 1
