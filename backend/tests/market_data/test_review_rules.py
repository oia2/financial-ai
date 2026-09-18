"""Правила, найденные внешним ревью: каждое — вместе со своей обратной формой.

Тесты здесь написаны по одному образцу. Сначала проверяется правило, потом —
ФОРМА, В КОТОРОЙ ОНО НАРУШАЛОСЬ: дефекты этой фичи раз за разом выживали
именно потому, что проверялся только прямой случай, а зеркальный оставался
непокрытым (spec 008, FR-047…FR-056).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest, plan
from financial_ai.market_data.interrupt import SourceStoppedError
from financial_ai.market_data.models import FuturesPosition, IngestRun
from financial_ai.market_data.repository import DailyBar, MarketDataRepository, PositionRow
from financial_ai.market_data.sources import equity_agg, positions, reference, securities
from financial_ai.ranking.dataset import _serialize_positions
from tests.market_data.conftest import FakePositionsClient, FakeSnapshot, NoInstrumentChanges

SESSION = dt.date(2026, 8, 28)
EARLIER = dt.date(2026, 8, 27)


@pytest.fixture
def settings() -> Settings:
    return Settings()


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


class CountingIss(NoInstrumentChanges):
    """Биржа, считающая обращения по каждому справочнику.

    Считать обращения — не любопытство: ровно этим измеряется правило «раз в
    сутки». Догон в восемьдесят сессий иначе платит сто шестьдесят обращений за
    ответ, который не менялся (FR-055).
    """

    def __init__(self, sessions: list[dt.date] | None = None) -> None:
        self.sessions = sessions or [SESSION]
        self.sector_calls = 0
        self.lot_calls = 0
        self.quote_calls: list[str] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        return [{"TRADEDATE": d.isoformat()} for d in self.sessions]

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        if "OPEN" not in columns:
            return []
        self.quote_calls.append(session_date)
        return [_quote("SBER", dt.date.fromisoformat(session_date))]

    async def fetch_index_titles(self) -> dict[str, str]:
        self.sector_calls += 1
        return {}

    async def fetch_index_analytics(
        self, index_id: str, session_date: str | None = None
    ) -> list[dict[str, object]]:
        # Непустой ответ обязателен: справочник без единой бумаги — неуспех, а
        # неуспех правомерно спрашивают снова, и суточный гейт тогда нечего
        # было бы мерить.
        return [{"ticker": "SBER", "weight": "10.5", "tradedate": SESSION.isoformat()}]

    async def fetch_equity_lot_sizes(self) -> dict[str, int]:
        self.lot_calls += 1
        return {"SBER": 10}


# --- FR-055: справочники текущего состояния спрашиваются раз в сутки ----------


@pytest.mark.db
async def test_справочники_спрашиваются_раз_в_сутки(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    iss = CountingIss([EARLIER, SESSION])

    await ingest.ingest_session(db_session, settings, EARLIER, client=iss, cbr_client=cbr_client)
    await ingest.ingest_session(db_session, settings, SESSION, client=iss, cbr_client=cbr_client)

    assert iss.sector_calls == 1
    assert iss.lot_calls == 1


@pytest.mark.db
async def test_котировки_при_этом_идут_на_каждую_сессию(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Обратная форма: суточный гейт не должен проредить посессионные источники."""
    iss = CountingIss([EARLIER, SESSION])

    await ingest.ingest_session(db_session, settings, EARLIER, client=iss, cbr_client=cbr_client)
    await ingest.ingest_session(db_session, settings, SESSION, client=iss, cbr_client=cbr_client)

    assert iss.quote_calls == [EARLIER.isoformat(), SESSION.isoformat()]


@pytest.mark.db
async def test_справочник_без_единого_исхода_спрашивается(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)

    assert await ingest.reference_is_due(repository, reference.SECTORS_SOURCE_ID, SESSION) is True
    assert await ingest.reference_is_due(repository, securities.SOURCE_ID, SESSION) is True


def test_справочники_объявлены_суточными_в_плане() -> None:
    """План на экране обязан называть их тем, чем они стали."""
    scopes = {spec.source_id: spec.scope for spec in plan.for_mode(plan.MODE_DAILY)}

    assert scopes["equity_sectors"] == plan.DAILY
    assert scopes["equity_lot_sizes"] == plan.DAILY
    assert scopes["equity_d1"] == plan.SESSION


# --- FR-052: один идентификатор на весь прогон -------------------------------


@pytest.mark.db
async def test_исходы_двух_сессий_живут_под_одним_прогоном(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    iss = CountingIss([EARLIER, SESSION])

    first = await ingest.ingest_session(
        db_session, settings, EARLIER, client=iss, cbr_client=cbr_client
    )
    await ingest.ingest_session(
        db_session, settings, SESSION, client=iss, cbr_client=cbr_client, run_id=first.run_id
    )
    await db_session.commit()

    quotes = [
        row for row in await _runs_of(db_session, first.run_id) if row.source_id == "equity_d1"
    ]

    assert sorted(row.session_date for row in quotes if row.session_date) == [EARLIER, SESSION]


@pytest.mark.db
async def test_отдельный_прогон_остаётся_отдельным(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Обратная форма: общий ключ не должен склеить два разных прогона."""
    iss = CountingIss([EARLIER, SESSION])

    first = await ingest.ingest_session(
        db_session, settings, EARLIER, client=iss, cbr_client=cbr_client
    )
    second = await ingest.ingest_session(
        db_session, settings, SESSION, client=iss, cbr_client=cbr_client
    )
    await db_session.commit()

    assert first.run_id != second.run_id
    assert await _runs_of(db_session, second.run_id)


async def _runs_of(session: AsyncSession, run_id: str) -> list[IngestRun]:
    rows = await session.scalars(select(IngestRun).where(IngestRun.run_id == run_id))
    return list(rows.all())


# --- FR-048: сущность важнее имени -------------------------------------------


def test_агрегаты_ложатся_в_ряд_переименованной_бумаги() -> None:
    rows = [{"SECID": "SBERX", "VALUE": "100", "NUMTRADES": "5", "WAPRICE": "314"}]

    out = equity_agg.rows_to_aggregates(rows, SESSION, {"SBERX": "EQ_AST_SBER"})

    assert out[0].asset_id == "EQ_AST_SBER"
    assert out[0].price_series_id == "EQ_PRS_SBER"


def test_бумага_без_псевдонима_ключуется_своим_именем() -> None:
    """Обратная форма: подмена не должна трогать бумагу, которую не переименовывали."""
    rows = [{"SECID": "GAZP", "VALUE": "100", "NUMTRADES": "5", "WAPRICE": "140"}]

    out = equity_agg.rows_to_aggregates(rows, SESSION, {"SBERX": "EQ_AST_SBER"})

    assert out[0].asset_id == "EQ_AST_GAZP"


# --- FR-053: позиции — только у торговавшихся бумаг --------------------------


@pytest.mark.db
async def test_ушедшая_с_торгов_бумага_не_спрашивается(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await _seed(repository, "DOMRF", "DOMR", traded=False)
    await db_session.commit()

    client = FakePositionsClient(
        available={("SBRF", SESSION): FakeSnapshot(), ("DOMR", SESSION): FakeSnapshot()}
    )
    await positions.sync_positions(client, repository, SESSION)  # type: ignore[arg-type]

    assert [code for code, _ in client.calls] == ["SBRF"]


@pytest.mark.db
async def test_торговавшаяся_бумага_со_связью_спрашивается(db_session: AsyncSession) -> None:
    """Обратная форма: отсев по торгам не должен выкинуть обычную бумагу."""
    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await db_session.commit()

    client = FakePositionsClient(available={("SBRF", SESSION): FakeSnapshot()})
    written = await positions.sync_positions(client, repository, SESSION)  # type: ignore[arg-type]

    assert written == 1


# --- FR-050: прерванный источник — не успех ----------------------------------


@pytest.mark.db
async def test_прерванный_сбор_позиций_объявляет_себя_прерванным(
    db_session: AsyncSession,
) -> None:
    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await _seed(repository, "GAZP", "GAZR", traded=True)
    await db_session.commit()

    client = FakePositionsClient(
        available={("SBRF", SESSION): FakeSnapshot(), ("GAZR", SESSION): FakeSnapshot()}
    )
    seen = {"n": 0}

    def should_stop() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    with pytest.raises(SourceStoppedError):
        await positions.sync_positions(  # type: ignore[arg-type]
            client, repository, SESSION, should_stop=should_stop
        )


@pytest.mark.db
async def test_собранное_до_остановки_сохраняется(db_session: AsyncSession) -> None:
    """Остановка отменяет утверждение о полноте дня, а не запись собранного."""
    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await _seed(repository, "GAZP", "GAZR", traded=True)
    await db_session.commit()

    client = FakePositionsClient(
        available={("SBRF", SESSION): FakeSnapshot(), ("GAZR", SESSION): FakeSnapshot()}
    )
    seen = {"n": 0}

    def should_stop() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    with pytest.raises(SourceStoppedError):
        await positions.sync_positions(  # type: ignore[arg-type]
            client, repository, SESSION, should_stop=should_stop
        )

    assert len(await repository.positions_for_window([SESSION])) == 1


@pytest.mark.db
async def test_прерванный_исход_не_закрывает_сессию(db_session: AsyncSession) -> None:
    """Суть дефекта: исход «ок» закрывал день навсегда, спросив три бумаги из ста двадцати."""
    repository = MarketDataRepository(db_session)
    now = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-stopped",
        source_id=positions.SOURCE_ID,
        status=ingest.STATUS_STOPPED,
        started_at=now,
        finished_at=now,
        session_date=SESSION,
    )
    await db_session.commit()

    assert await repository.sessions_with_successful_run([SESSION], positions.SOURCE_ID) == set()


@pytest.mark.db
async def test_успешный_исход_сессию_по_прежнему_закрывает(db_session: AsyncSession) -> None:
    """Обратная форма: новый исход не должен обесценить настоящий успех."""
    repository = MarketDataRepository(db_session)
    now = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-ok",
        source_id=positions.SOURCE_ID,
        status=ingest.STATUS_OK,
        started_at=now,
        finished_at=now,
        session_date=SESSION,
    )
    await db_session.commit()

    assert await repository.sessions_with_successful_run([SESSION], positions.SOURCE_ID) == {
        SESSION
    }


# --- FR-051: слепок различает контракты --------------------------------------


def test_слепок_различает_контракты_одной_бумаги() -> None:
    rows = [
        _position("EQ_AST_SBER", "SBRF", Decimal("10")),
        _position("EQ_AST_SBER", "SBER_NEW", Decimal("20")),
    ]

    out = _serialize_positions(rows, [SESSION])

    assert set(out) == {("EQ_AST_SBER", "SBRF"), ("EQ_AST_SBER", "SBER_NEW")}
    assert out[("EQ_AST_SBER", "SBRF")][0][0] == "10"
    assert out[("EQ_AST_SBER", "SBER_NEW")][0][0] == "20"


def test_бумага_с_одним_контрактом_остаётся_одним_рядом() -> None:
    """Обратная форма: разделение по контракту не должно плодить ряды на ровном месте."""
    rows = [_position("EQ_AST_SBER", "SBRF", Decimal("10"))]

    out = _serialize_positions(rows, [SESSION])

    assert list(out) == [("EQ_AST_SBER", "SBRF")]


def _position(asset_id: str, contract: str, value: Decimal) -> FuturesPosition:
    return FuturesPosition(
        asset_id=asset_id,
        session_date=SESSION,
        contract_code=contract,
        fiz_long=value,
        fiz_short=None,
        jur_long=None,
        jur_short=None,
    )


async def _seed(repository: MarketDataRepository, ticker: str, contract: str, traded: bool) -> None:
    """Бумага со связью и историей позиций — торгующаяся или ушедшая с торгов.

    Разница между ними ровно одна: наблюдение за эту сессию. Именно по нему
    источник и решает, кого спрашивать (FR-053).
    """
    asset_id = f"EQ_AST_{ticker}"
    series_id = f"EQ_PRS_{ticker}"
    await repository.add_trading_sessions([SESSION])
    await repository.upsert_asset(asset_id, ticker, SESSION)
    await repository.upsert_price_series(series_id, asset_id, SESSION)
    await repository.open_link(
        asset_id=asset_id,
        contract_code=contract,
        valid_from=SESSION - dt.timedelta(days=365),
        chosen_by="underlying_and_emitter",
    )
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id=asset_id,
                price_series_id=series_id,
                session_date=SESSION,
                open=Decimal("1") if traded else None,
                high=None,
                low=None,
                close=Decimal("1") if traded else None,
                volume=None,
            )
        ]
    )
    await repository.upsert_positions(
        [
            PositionRow(
                asset_id=asset_id,
                session_date=SESSION - dt.timedelta(days=30),
                contract_code=contract,
                fiz_long=Decimal("1"),
                fiz_short=None,
                jur_long=None,
                jur_short=None,
            )
        ]
    )


# --- план на экране и код сбора не расходятся --------------------------------


@pytest.mark.db
async def test_план_ежедневного_прогона_совпадает_с_кодом_сбора(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """План — обещание человеку, и оно обязано равняться работе.

    Утверждение «порядок здесь тот же самый, которым идёт сбор» жило в
    комментарии `plan.py`, а проверки под ним не было: источник, выпавший из
    цикла, оставался в плане вечно ожидающим и никого не смущал (FR-056).
    """
    seen: list[str] = []

    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=CountingIss([SESSION]),
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        on_source=lambda source_id, status, outcome: seen.append(source_id),
    )

    planned = {spec.source_id for spec in plan.for_mode(plan.MODE_DAILY)}
    # Календарь объявляет свой исход в `advance`, а не внутри сессии: он один
    # на прогон, а не один на сессию.
    assert planned - {"trading_calendar"} == set(seen) - {"trading_calendar"}
