"""Тесты цикла сбора: идемпотентность, гейт календаря, устойчивость к сбоям.

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
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import equity_d1

pytestmark = pytest.mark.db

SESSION = dt.date(2026, 8, 28)
WEEKEND = dt.date(2026, 8, 29)


def _quote(secid: str, close: str) -> dict[str, object]:
    return {
        "SECID": secid,
        "TRADEDATE": SESSION.isoformat(),
        "OPEN": "312.4",
        "HIGH": "315.1",
        "LOW": "311.0",
        "CLOSE": close,
        "VOLUME": "1000",
    }


class FakeIss:
    """Подделка клиента биржи. Считает обращения — это проверяемое поведение."""

    def __init__(
        self,
        calendar_dates: list[dt.date] | None = None,
        quotes: list[dict[str, object]] | None = None,
        fail_quotes: bool = False,
    ) -> None:
        self.calendar_dates = calendar_dates or [SESSION]
        self.quotes = quotes if quotes is not None else [_quote("SBER", "314.22")]
        self.fail_quotes = fail_quotes
        self.session_calls: list[str] = []
        self.quote_calls: list[str] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        return [{"TRADEDATE": d.isoformat()} for d in self.calendar_dates]

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.session_calls.append(session_date)
        # Котировки узнаются по набору колонок: у остальных источников он свой.
        if "OPEN" not in columns:
            return []
        self.quote_calls.append(session_date)
        if self.fail_quotes:
            raise IssError("биржа недоступна")
        return self.quotes


@pytest.fixture
def settings() -> Settings:
    return Settings()


# --- идемпотентность ---------------------------------------------------------


async def test_ingest_writes_quotes(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(SESSION) == 1


async def test_repeated_ingest_does_not_duplicate(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-006, SC-002: повторный сбор за ту же дату не меняет число наблюдений."""
    repository = MarketDataRepository(db_session)

    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    first = await repository.count_daily_bars(SESSION)

    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    second = await repository.count_daily_bars(SESSION)

    assert first == second == 1


async def test_repeated_ingest_keeps_values(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    for _ in range(2):
        await ingest.ingest_session(
            db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
        )

    repository = MarketDataRepository(db_session)
    bars = await repository.daily_bars_for_window([SESSION])
    assert bars[0].close == Decimal("314.22")


async def test_exchange_correction_is_applied(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Переиздание биржей применяется — но не молча: расхождение попадает в журнал."""
    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    corrected = FakeIss(quotes=[_quote("SBER", "999.99")])
    await ingest.ingest_session(
        db_session, settings, SESSION, client=corrected, cbr_client=cbr_client
    )

    repository = MarketDataRepository(db_session)
    bars = await repository.daily_bars_for_window([SESSION])
    assert len(bars) == 1
    assert bars[0].close == Decimal("999.99")


# --- гейт календаря ----------------------------------------------------------


async def test_non_trading_day_makes_no_exchange_call(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-003, SC-003: в выходной за котировками не ходим вовсе."""
    iss = FakeIss(calendar_dates=[SESSION])
    result = await ingest.ingest_session(
        db_session, settings, WEEKEND, client=iss, cbr_client=cbr_client
    )

    assert iss.session_calls == []
    quotes = [o for o in result.outcomes if o.source_id == equity_d1.SOURCE_ID]
    assert quotes[0].status == ingest.STATUS_SKIPPED


async def test_non_trading_day_writes_no_bars(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await ingest.ingest_session(
        db_session, settings, WEEKEND, client=FakeIss(), cbr_client=cbr_client
    )
    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(WEEKEND) == 0


async def test_calendar_runs_first(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """До календаря неизвестно, была ли сессия — он обязан идти первым."""
    result = await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    assert result.outcomes[0].source_id == "trading_calendar"


async def test_call_count_does_not_grow_with_universe(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-004: число обращений к бирже не зависит от числа бумаг.

    Перенос парсера «как есть» дал бы по обращению на каждую бумагу. Здесь
    проверяется именно это: удвоение состава доски не удваивает число запросов.
    """
    few = FakeIss(quotes=[_quote(t, "1") for t in ("SBER", "GAZP")])
    await ingest.ingest_session(db_session, settings, SESSION, client=few, cbr_client=cbr_client)

    many = FakeIss(quotes=[_quote(f"T{i:03d}", "1") for i in range(40)])
    await ingest.ingest_session(db_session, settings, SESSION, client=many, cbr_client=cbr_client)

    assert len(few.session_calls) == len(many.session_calls)
    assert len(few.quote_calls) == 1


async def test_all_tickers_are_stored(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    iss = FakeIss(quotes=[_quote(t, "1") for t in ("SBER", "GAZP", "LKOH", "GMKN")])
    await ingest.ingest_session(db_session, settings, SESSION, client=iss, cbr_client=cbr_client)

    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(SESSION) == 4


# --- устойчивость ------------------------------------------------------------


async def test_exchange_failure_is_recorded(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-007: неуспех фиксируется с причиной."""
    result = await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(fail_quotes=True), cbr_client=cbr_client
    )
    assert not result.succeeded
    assert equity_d1.SOURCE_ID in result.unfinished_sources
    failed = [o for o in result.outcomes if o.source_id == equity_d1.SOURCE_ID]
    assert "недоступна" in (failed[0].failure_reason or "")


async def test_exchange_failure_keeps_existing_data(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """SC-008: сбой биржи не портит ранее собранное."""
    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    repository = MarketDataRepository(db_session)
    before = await repository.count_daily_bars(SESSION)

    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(fail_quotes=True), cbr_client=cbr_client
    )

    assert before == await repository.count_daily_bars(SESSION) == 1


async def test_run_outcomes_are_recorded(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-008: незакрытые источники видны без чтения логов."""
    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(fail_quotes=True), cbr_client=cbr_client
    )
    repository = MarketDataRepository(db_session)
    runs = await repository.runs_for_session(SESSION)
    statuses = {r.source_id: r.status for r in runs}
    assert statuses[equity_d1.SOURCE_ID] == ingest.STATUS_FAILED


async def test_one_failed_source_does_not_stop_others(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Неудача одного источника не отменяет прочие: они независимы."""
    result = await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(fail_quotes=True), cbr_client=cbr_client
    )
    assert any(o.status == ingest.STATUS_OK for o in result.outcomes)


async def test_asset_and_series_are_registered(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    repository = MarketDataRepository(db_session)
    bars = await repository.daily_bars_for_window([SESSION])
    assert bars[0].asset_id == "EQ_AST_SBER"
    assert bars[0].price_series_id == "EQ_PRS_SBER"


# --- макроряды ЦБ ------------------------------------------------------------


async def test_cbr_series_are_stored(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Макроряды ЦБ относятся к текущей сессии: они публикуются до закрытия."""
    await ingest.ingest_session(
        db_session, settings, SESSION, client=FakeIss(), cbr_client=cbr_client
    )
    repository = MarketDataRepository(db_session)
    rows = await repository.global_values_for_window([SESSION])
    series = {r.series_id for r in rows}
    assert "CBR_KEY_RATE" in series
    assert any(s.startswith("CBR_ZCYC_") for s in series)
