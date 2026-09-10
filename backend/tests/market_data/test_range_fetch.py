"""Тесты диапазонных обращений при догоне.

Смысл проверки: у половины источников дыра любой длины закрывается **одним**
обращением. Клиент это уже умел — ежедневный добор сужал диапазон до одного дня
осознанно, и для догона такое сужение было бы чистой потерей: D обращений на
каждый ряд вместо одного.

Если тест сломается, значит кто-то вернул перебор дат туда, где биржа отдаёт
период целиком.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import global_series

pytestmark = pytest.mark.db

SHORT = [dt.date(2026, 8, 26), dt.date(2026, 8, 27), dt.date(2026, 8, 28)]
LONG = SHORT + [dt.date(2026, 8, 31) + dt.timedelta(days=n) for n in range(12)]


class CountingIss:
    """Подделка биржи, считающая обращения по видам."""

    def __init__(self, sessions: list[dt.date]) -> None:
        self.sessions = sessions
        self.history_calls: list[tuple[str, str, str]] = []
        self.session_calls: list[str] = []

    async def fetch_security_history(
        self,
        secid: str,
        date_from: str,
        date_till: str,
        columns: tuple[str, ...],
        **kwargs: object,
    ) -> list[dict[str, object]]:
        self.history_calls.append((secid, date_from, date_till))
        return [{"TRADEDATE": d.isoformat(), "CLOSE": "3200.5"} for d in self.sessions]

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.session_calls.append(session_date)
        if "OPEN" not in columns:
            return []
        return [
            {
                "SECID": "SBER",
                "TRADEDATE": session_date,
                "OPEN": "312.4",
                "HIGH": "315.1",
                "LOW": "311.0",
                "CLOSE": "314.22",
                "VOLUME": "1000",
            }
        ]

    async def fetch_session_rows_for(
        self, session_date: str, columns: tuple[str, ...], **kwargs: object
    ) -> list[dict[str, object]]:
        self.session_calls.append(session_date)
        return []


async def _seed(session: AsyncSession, sessions: list[dt.date]) -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(sessions)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", sessions[-1])
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", sessions[-1])
    # Собрана только первая сессия: остальное — дыра.
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=sessions[0],
                open=Decimal("312.4"),
                high=Decimal("315.1"),
                low=Decimal("311.0"),
                close=Decimal("314.22"),
                volume=Decimal("1000"),
            )
        ]
    )
    await session.commit()


async def _count_history_calls(
    session: AsyncSession, sessions: list[dt.date], cbr_client: httpx.AsyncClient
) -> int:
    await _seed(session, sessions)
    settings = Settings(market_data_catchup_window_sessions=len(sessions))
    iss = CountingIss(sessions)
    await ingest.catch_up(session, settings, sessions[-1], iss, cbr_client)
    return len(iss.history_calls)


async def test_range_calls_do_not_grow_with_gap_length(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient
) -> None:
    """Глобальные ряды: одно обращение на ряд независимо от длины дыры."""
    short = await _count_history_calls(db_session, SHORT, cbr_client)

    # Свежая база под второй замер: иначе первая уже закрыла дыру.
    await db_session.rollback()
    for table in ("market_equity_daily_bar", "market_ingest_run", "market_trading_session"):
        await db_session.execute(text(f"DELETE FROM {table}"))
    await db_session.commit()

    long = await _count_history_calls(db_session, LONG, cbr_client)

    assert short == long
    assert short == len(global_series.ISS_SERIES)


async def test_per_date_calls_do_grow(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient
) -> None:
    """А источники с выборкой по дате дорожают: это ожидаемо и честно."""
    await _seed(db_session, SHORT)
    settings = Settings(market_data_catchup_window_sessions=len(SHORT))
    iss = CountingIss(SHORT)

    await ingest.catch_up(db_session, settings, SHORT[-1], iss, cbr_client)

    # Две пропущенные сессии, по несколько источников на каждую.
    assert len(iss.session_calls) > len(SHORT) - 1


async def test_range_covers_the_whole_gap(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient
) -> None:
    """Границы обращения совпадают с границами дыры, а не с одной датой."""
    await _seed(db_session, SHORT)
    settings = Settings(market_data_catchup_window_sessions=len(SHORT))
    iss = CountingIss(SHORT)

    await ingest.catch_up(db_session, settings, SHORT[-1], iss, cbr_client)

    ranges = {(f, t) for _, f, t in iss.history_calls}
    assert (SHORT[1].isoformat(), SHORT[2].isoformat()) in ranges


# --- бесполезные обращения (FR-022, FR-023, SC-007) --------------------------


async def test_cbr_is_asked_once_per_run_not_once_per_session(
    db_session: AsyncSession,
) -> None:
    """Страницы ЦБ принимают период — перебор дат был бы потерей.

    Считаются настоящие обращения к cbr.ru: две страницы, ключевая ставка и
    кривая доходности, независимо от того, сколько сессий в дыре.
    """
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        from financial_ai.market_data.sources import cbr as cbr_module
        from tests.market_data.conftest import KEY_RATE_HTML, ZCYC_HTML

        if cbr_module.ZCYC_URL in str(request.url):
            return httpx.Response(200, text=ZCYC_HTML)
        return httpx.Response(200, text=KEY_RATE_HTML)

    await _seed(db_session, LONG)
    settings = Settings(market_data_catchup_window_sessions=len(LONG))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await ingest.catch_up(db_session, settings, LONG[-1], CountingIss(LONG), client)

    assert len(requests) == 2


async def test_range_sources_are_not_asked_per_session(db_session: AsyncSession) -> None:
    """Обращения по датам не касаются рядов, отдающих период целиком."""
    await _seed(db_session, LONG)
    settings = Settings(market_data_catchup_window_sessions=len(LONG))
    iss = CountingIss(LONG)
    silent = httpx.MockTransport(lambda request: httpx.Response(200, text=""))
    client = httpx.AsyncClient(transport=silent)

    await ingest.catch_up(db_session, settings, LONG[-1], iss, client)

    # Диапазонных обращений ровно столько, сколько рядов: длина дыры на них
    # не влияет, хотя сессий в ней четырнадцать.
    assert len(iss.history_calls) == len(global_series.ISS_SERIES)
    assert len(iss.session_calls) > len(iss.history_calls)
