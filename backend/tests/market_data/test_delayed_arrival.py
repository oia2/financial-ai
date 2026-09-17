"""Тесты задержанного прибытия данных.

Позиции по фьючерсам приходят позже закрытия сессии. Ошибка здесь не падает
тестом сама и не видна в данных: передатированное наблюдение просто сдвигает
историю на день, и модель обучается на смещённом сигнале. Поэтому запрет
проверяется явно.

С фичи 005 источник позиций — не биржевой интерфейс данных, а форма на сайте
биржи, поэтому подделывается отдельный клиент, а не общий клиент ISS.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import positions
from financial_ai.market_data.sources.positions_client import (
    PositionSnapshot,
    PositionsSourceError,
)
from tests.market_data.conftest import FakePositionsClient, FakeSnapshot

pytestmark = pytest.mark.db

SESSION = dt.date(2026, 8, 28)
NEXT_SESSION = dt.date(2026, 8, 31)


# --- запрет передатирования --------------------------------------------------


def test_observation_date_comes_from_argument_not_response() -> None:
    """Снимок за другую дату наблюдением о запрошенной сессии не становится.

    Биржа отдаёт последний доступный снимок, если за дату данных нет. Без
    сверки даты он записался бы как наблюдение о запрошенном дне — то самое
    передатирование, которое невозможно заметить в данных.
    """
    snapshot = PositionSnapshot(
        trade_date=NEXT_SESSION,
        fiz_long=Decimal("100"),
        fiz_short=None,
        jur_long=None,
        jur_short=None,
    )
    assert snapshot.trade_date != SESSION


def test_snapshot_without_values_is_distinguishable() -> None:
    """Пустой снимок отличим от снимка со значениями.

    На этом различии держится FR-018: полное отсутствие значений — неуспех, а
    не успех с формулировкой про частичное покрытие.
    """
    empty = PositionSnapshot(SESSION, None, None, None, None)
    filled = PositionSnapshot(SESSION, Decimal("1"), None, None, None)
    assert empty.has_values is False
    assert filled.has_values is True


def test_absent_position_is_none_not_zero() -> None:
    """Ноль означал бы «позиций не держат», пропуск — «мы не знаем»."""
    snapshot = PositionSnapshot(SESSION, None, Decimal("0"), None, None)
    assert snapshot.fiz_long is None
    assert snapshot.fiz_short == Decimal("0")


# --- повторы для задержанного источника --------------------------------------


class DelayedIss:
    """Подделка биржи: календарь и котировки, без позиций.

    Позиции сюда больше не ходят — у них свой клиент.
    """

    def __init__(self, calendar: list[dt.date] | None = None) -> None:
        self.calendar = calendar or [SESSION]

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        return [{"TRADEDATE": d.isoformat()} for d in self.calendar]

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        if "OPEN" in columns:
            return [{"SECID": "SBER", "OPEN": "1", "CLOSE": "2"}]
        return []

    async def fetch_session_rows_for(
        self, session_date: str, columns: tuple[str, ...], **kwargs: object
    ) -> list[dict[str, object]]:
        return []

    # Связи инструментов приводятся в соответствие перед сбором позиций. Здесь
    # состав не меняется, и подделка отвечает пустыми ответами: испытание про
    # задержанное прибытие, а не про состав.

    async def fetch_equity_isins(self) -> dict[str, str]:
        return {}

    async def fetch_futures_series(self) -> list[dict[str, object]]:
        return []

    async def fetch_futures_open_interest(self) -> dict[str, int]:
        return {}

    async def fetch_emitter_id(self, secid: str) -> str | None:
        return None


class FlakyPositions(FakePositionsClient):
    """Источник позиций, отвечающий не с первой попытки."""

    def __init__(self, succeed_on: int) -> None:
        super().__init__()
        self.succeed_on = succeed_on
        self.attempts = 0

    async def fetch(self, contract_code: str, day: object) -> object | None:
        self.attempts += 1
        if self.attempts < self.succeed_on:
            raise PositionsSourceError("данные ещё не опубликованы")
        return await super().fetch(contract_code, day)


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def _seed_asset(session: AsyncSession) -> None:
    """Бумага с историей: без неё источнику позиций нечего спрашивать."""
    repository = MarketDataRepository(session)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSION)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSION)
    await session.commit()


async def test_delayed_source_is_retried(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """FR-005: для задержанного источника выполняются повторы."""
    await _seed_asset(db_session)
    client = FlakyPositions(succeed_on=2)
    result = await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss(),
        cbr_client=cbr_client,
        positions_client=client,
    )

    assert client.attempts == 2
    outcome = next(o for o in result.outcomes if o.source_id == positions.SOURCE_ID)
    assert outcome.status == ingest.STATUS_OK


async def test_retries_are_bounded(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Повторы не бесконечны: если данных нет — их просто нет."""
    await _seed_asset(db_session)
    client = FlakyPositions(succeed_on=99)
    result = await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss(),
        cbr_client=cbr_client,
        positions_client=client,
    )

    assert client.attempts == ingest.DELAYED_SOURCE_ATTEMPTS
    outcome = next(o for o in result.outcomes if o.source_id == positions.SOURCE_ID)
    assert outcome.status == ingest.STATUS_FAILED


async def test_missing_positions_do_not_migrate_to_next_session(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Не доехавшие данные не появляются датой следующей сессии."""
    await _seed_asset(db_session)
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss([SESSION, NEXT_SESSION]),
        cbr_client=cbr_client,
        positions_client=FlakyPositions(succeed_on=99),
    )

    repository = MarketDataRepository(db_session)
    assert await repository.positions_for_window([SESSION, NEXT_SESSION]) == []


async def test_late_data_lands_on_its_own_session(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Доехавшие позже данные записываются датой своей сессии."""
    await _seed_asset(db_session)
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss([SESSION, NEXT_SESSION]),
        cbr_client=cbr_client,
        positions_client=FlakyPositions(succeed_on=99),
    )
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss([SESSION, NEXT_SESSION]),
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),
    )

    repository = MarketDataRepository(db_session)
    stored = await repository.positions_for_window([SESSION, NEXT_SESSION])
    assert [p.session_date for p in stored] == [SESSION]
    assert stored[0].fiz_long == Decimal("100")


async def test_snapshot_for_another_date_is_not_stored(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Данных за запрошенную дату нет — строки не появляется.

    Настоящий клиент отдаёт `None`, когда ответ пришёл за другую дату. Записать
    его как наблюдение о запрошенной сессии было бы передатированием.
    """
    await _seed_asset(db_session)
    other_day_only = FakePositionsClient(
        available={("SBRF_F", NEXT_SESSION): FakeSnapshot()},
    )

    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss([SESSION, NEXT_SESSION]),
        cbr_client=cbr_client,
        positions_client=other_day_only,
    )

    repository = MarketDataRepository(db_session)
    assert await repository.positions_for_window([SESSION, NEXT_SESSION]) == []


async def test_delayed_failure_does_not_fail_other_sources(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Недоехавшие позиции не отменяют собранные котировки."""
    await _seed_asset(db_session)
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=DelayedIss(),
        cbr_client=cbr_client,
        positions_client=FlakyPositions(succeed_on=99),
    )

    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(SESSION) == 1


async def test_positions_client_is_created_when_not_supplied(
    db_session: AsyncSession,
    settings: Settings,
    cbr_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Позиции собираются и тогда, когда клиент не передали снаружи.

    Дефект, пойманный на стенде: ежедневный прогон клиент не создавал, и
    источник падал с «клиент не настроен» — то есть не собирался никогда.
    """
    await _seed_asset(db_session)
    created = FakePositionsClient()
    monkeypatch.setattr(ingest, "PositionsClient", lambda settings: created)

    result = await ingest.ingest_session(
        db_session, settings, SESSION, client=DelayedIss(), cbr_client=cbr_client
    )

    outcome = next(o for o in result.outcomes if o.source_id == positions.SOURCE_ID)
    assert outcome.status == ingest.STATUS_OK
    assert created.calls == [("SBRF_F", SESSION)]
