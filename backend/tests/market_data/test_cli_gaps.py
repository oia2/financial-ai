"""Тесты команды просмотра пропусков.

Требование FR-018 буквально: перечень пропущенных сессий должен быть доступен
**без чтения логов**. Поэтому проверяется именно вывод команды, а не внутреннее
состояние — сегодня дыру нельзя обнаружить иначе как случайно, и вывод здесь и
есть способ её обнаружить.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import get_settings
from financial_ai.market_data import cli
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import equity_d1

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
]
ASOF = SESSIONS[-1]


@pytest.fixture(autouse=True)
def _window() -> None:
    """Окно поиска сужено до засеянных сессий: иначе в него попадёт пустота."""
    get_settings.cache_clear()


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


async def test_gaps_lists_missing_sessions(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[3]])

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "пропущено сессий: 2" in out
    assert SESSIONS[1].isoformat() in out
    assert SESSIONS[2].isoformat() in out


async def test_gaps_reports_full_window(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _seed(db_session, SESSIONS)

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "пропущенных сессий нет" in out


async def test_gaps_reports_empty_storage(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустое хранилище — не дыра: это первичная загрузка."""
    await _seed(db_session, [])

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "нужна первичная загрузка" in out


async def test_gaps_shows_unfinished_sources_with_reason(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Незакрытый источник виден так же, как сбой сбора, и с причиной."""
    repository = await _seed(db_session, SESSIONS)
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id="futures_positions",
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[1],
        failure_reason="источник не ответил",
        trigger="catchup",
    )
    await db_session.commit()

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "незакрыто по источникам:" in out
    assert "futures_positions" in out
    assert "источник не ответил" in out


async def test_gaps_without_calendar_fails(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli._gaps(None)
    out = capsys.readouterr().out

    assert code == 1
    assert "календарь пуст" in out


async def test_stats_shows_what_triggered_the_run(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Иначе прогон догона за вчера неотличим от обычного."""
    repository = await _seed(db_session, SESSIONS)
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id=equity_d1.SOURCE_ID,
        status="ok",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[1],
        rows_written=288,
        trigger="catchup",
    )
    await db_session.commit()

    code = await cli._stats(SESSIONS[1])
    out = capsys.readouterr().out

    assert code == 0
    assert "catchup" in out


async def test_catchup_dry_run_touches_nothing(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Показывает, что было бы собрано, и к источникам не обращается."""
    await _seed(db_session, [SESSIONS[0], SESSIONS[3]])

    code = await cli._catchup(ASOF, dry_run=True)
    out = capsys.readouterr().out

    assert code == 0
    assert "было бы собрано сессий: 2" in out

    repository = MarketDataRepository(db_session)
    assert await repository.sessions_with_daily_bars(SESSIONS) == {SESSIONS[0], SESSIONS[3]}
