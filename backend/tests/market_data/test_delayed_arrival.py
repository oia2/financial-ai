"""Тесты задержанного прибытия данных.

Позиции по фьючерсам приходят позже закрытия сессии. Ошибка здесь не падает
тестом сама и не видна в данных: передатированное наблюдение просто сдвигает
историю на день, и модель обучается на смещённом сигнале. Поэтому запрет
проверяется явно.
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
from financial_ai.market_data.sources import positions

pytestmark = pytest.mark.db

SESSION = dt.date(2026, 8, 28)
NEXT_SESSION = dt.date(2026, 8, 31)


# --- запрет передатирования --------------------------------------------------


def test_observation_date_comes_from_argument_not_response() -> None:
    """Дата берётся из запроса, а не из ответа биржи.

    Так передатирование становится невозможным по построению, а не по
    договорённости, которую легко нарушить при следующей правке.
    """
    rows = [{"SECID": "SBER", "TRADEDATE": NEXT_SESSION.isoformat(), "FIZ_LONG": "100"}]
    parsed = positions.rows_to_positions(rows, SESSION)
    assert parsed[0].session_date == SESSION
    assert parsed[0].session_date != NEXT_SESSION


def test_late_arrival_keeps_its_own_date() -> None:
    """Опоздавшее наблюдение остаётся наблюдением о своём дне."""
    rows = [{"SECID": "SBER", "FIZ_LONG": "100", "JUR_SHORT": "50"}]
    parsed = positions.rows_to_positions(rows, SESSION)
    assert parsed[0].session_date == SESSION


def test_absent_position_is_none_not_zero() -> None:
    """Ноль означал бы «позиций не держат», пропуск — «мы не знаем»."""
    rows = [{"SECID": "SBER", "FIZ_LONG": None, "FIZ_SHORT": "", "JUR_LONG": "0"}]
    parsed = positions.rows_to_positions(rows, SESSION)
    assert parsed[0].fiz_long is None
    assert parsed[0].fiz_short is None
    assert parsed[0].jur_long == Decimal("0")


def test_partial_coverage_is_normal() -> None:
    """Позиции есть не по всем активам — это норма, а не сбой."""
    rows = [{"SECID": "SBER", "FIZ_LONG": "100"}]
    assert len(positions.rows_to_positions(rows, SESSION)) == 1


# --- повторы для задержанного источника --------------------------------------


class DelayedIss:
    """Подделка биржи, отдающая позиции не с первой попытки."""

    def __init__(self, succeed_on: int, calendar: list[dt.date] | None = None) -> None:
        self.succeed_on = succeed_on
        self.calendar = calendar or [SESSION]
        self.position_attempts = 0

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        return [{"TRADEDATE": d.isoformat()} for d in self.calendar]

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        if "FIZ_LONG" in columns:
            self.position_attempts += 1
            if self.position_attempts < self.succeed_on:
                raise IssError("данные ещё не опубликованы")
            return [{"SECID": "SBER", "FIZ_LONG": "100", "JUR_SHORT": "50"}]
        if "OPEN" in columns:
            return [{"SECID": "SBER", "OPEN": "1", "CLOSE": "2"}]
        return []


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def test_delayed_source_is_retried(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-005: для задержанного источника выполняются повторы."""
    iss = DelayedIss(succeed_on=2)
    result = await ingest.ingest_session(
        db_session, settings, SESSION, client=iss, cbr_client=cbr_client
    )

    assert iss.position_attempts == 2
    outcome = next(o for o in result.outcomes if o.source_id == positions.SOURCE_ID)
    assert outcome.status == ingest.STATUS_OK


async def test_retries_are_bounded(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Повторы не бесконечны: если данных нет — их просто нет."""
    iss = DelayedIss(succeed_on=99)
    result = await ingest.ingest_session(
        db_session, settings, SESSION, client=iss, cbr_client=cbr_client
    )

    assert iss.position_attempts == ingest.DELAYED_SOURCE_ATTEMPTS
    outcome = next(o for o in result.outcomes if o.source_id == positions.SOURCE_ID)
    assert outcome.status == ingest.STATUS_FAILED


async def test_missing_positions_do_not_migrate_to_next_session(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Не доехавшие данные не появляются датой следующей сессии."""
    iss = DelayedIss(succeed_on=99, calendar=[SESSION, NEXT_SESSION])
    await ingest.ingest_session(db_session, settings, SESSION, client=iss, cbr_client=cbr_client)

    repository = MarketDataRepository(db_session)
    assert await repository.positions_for_window([SESSION, NEXT_SESSION]) == []


async def test_late_data_lands_on_its_own_session(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Доехавшие позже данные записываются датой своей сессии."""
    late = DelayedIss(succeed_on=99, calendar=[SESSION, NEXT_SESSION])
    await ingest.ingest_session(db_session, settings, SESSION, client=late, cbr_client=cbr_client)

    arrived = DelayedIss(succeed_on=1, calendar=[SESSION, NEXT_SESSION])
    await ingest.ingest_session(
        db_session, settings, SESSION, client=arrived, cbr_client=cbr_client
    )

    repository = MarketDataRepository(db_session)
    stored = await repository.positions_for_window([SESSION, NEXT_SESSION])
    assert [p.session_date for p in stored] == [SESSION]
    assert stored[0].fiz_long == Decimal("100")


async def test_delayed_failure_does_not_fail_other_sources(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Недоехавшие позиции не отменяют собранные котировки."""
    iss = DelayedIss(succeed_on=99)
    await ingest.ingest_session(db_session, settings, SESSION, client=iss, cbr_client=cbr_client)

    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(SESSION) == 1
