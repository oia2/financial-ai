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


# --- FR-050: прерванный исход перевешивает наблюдения ------------------------


@pytest.mark.db
async def test_остановка_после_первой_бумаги_сессию_не_закрывает(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Сквозной случай ревью: остановка после первого из двух инструментов.

    Собранная строка остаётся — остановка не отменяет запись, — и раньше она
    закрывала сессию по правилу «есть непустое наблюдение». Отдельного исхода
    «прервано» самого по себе не хватало (FR-050).
    """
    from financial_ai.market_data import completeness, groups

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

    await ingest.run_source(
        repository,
        "run-stop",
        positions.SOURCE_ID,
        SESSION,
        lambda: positions.sync_positions(  # type: ignore[arg-type]
            client, repository, SESSION, should_stop=should_stop
        ),
    )
    await db_session.commit()

    # Строка собрана — и всё же сессия остаётся работой.
    assert len(await repository.positions_for_window([SESSION])) == 1
    missing = await completeness.missing_sessions(
        repository, groups.BY_ID[groups.GroupId.POSITIONS], [SESSION]
    )
    assert missing == [SESSION]


@pytest.mark.db
async def test_добранная_сессия_работой_быть_перестаёт(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Обратная форма: правило смотрит на ПОСЛЕДНИЙ исход источника."""
    from financial_ai.market_data import completeness, groups

    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await db_session.commit()

    client = FakePositionsClient(available={("SBRF", SESSION): FakeSnapshot()})
    await ingest.run_source(
        repository,
        "run-stop",
        positions.SOURCE_ID,
        SESSION,
        lambda: positions.sync_positions(client, repository, SESSION),  # type: ignore[arg-type]
    )
    await db_session.commit()

    missing = await completeness.missing_sessions(
        repository, groups.BY_ID[groups.GroupId.POSITIONS], [SESSION]
    )
    assert missing == []


# --- FR-049: сверка связей не датируется задним числом -----------------------


@pytest.mark.db
async def test_интервал_не_закрывается_раньше_своего_начала(
    db_session: AsyncSession,
) -> None:
    """Сквозной случай ревью: связь с 16-го, догон кончается 10-м.

    Смена семейства закрывала бы прежний интервал 9-м числом — раньше его
    собственного начала. Такой интервал не означает ничего (FR-049).
    """
    repository = MarketDataRepository(db_session)
    late = SESSION + dt.timedelta(days=19)
    early = SESSION + dt.timedelta(days=13)

    await repository.open_link("EQ_AST_SBER", "SBRF", late, "underlying_and_emitter")
    await db_session.commit()

    changed = await repository.open_link("EQ_AST_SBER", "SBER_NEW", early, "underlying_only")
    await db_session.commit()

    assert changed is False
    links = await repository.link_history("EQ_AST_SBER")
    assert [(link.contract_code, link.valid_from, link.valid_till) for link in links] == [
        ("SBRF", late, None)
    ]


@pytest.mark.db
async def test_смена_контракта_вперёд_по_времени_проходит(
    db_session: AsyncSession,
) -> None:
    """Обратная форма: запрет касается только утверждений задним числом."""
    repository = MarketDataRepository(db_session)
    later = SESSION + dt.timedelta(days=7)

    await repository.open_link("EQ_AST_SBER", "SBRF", SESSION, "underlying_and_emitter")
    await db_session.commit()

    changed = await repository.open_link("EQ_AST_SBER", "SBER_NEW", later, "underlying_only")
    await db_session.commit()

    assert changed is True
    assert await repository.active_links_on(later) == {"EQ_AST_SBER": "SBER_NEW"}


@pytest.mark.db
async def test_закрытие_раньше_начала_не_проходит(db_session: AsyncSession) -> None:
    """Инвариант держится в хранилище, а не на аккуратности вызывающего."""
    repository = MarketDataRepository(db_session)
    late = SESSION + dt.timedelta(days=19)

    await repository.open_link("EQ_AST_SBER", "SBRF", late, "underlying_and_emitter")
    await db_session.commit()

    closed = await repository.close_link("EQ_AST_SBER", SESSION)
    await db_session.commit()

    assert closed is False
    assert await repository.active_links_on(late) == {"EQ_AST_SBER": "SBRF"}


# --- FR-052: исход заводится до обращения ------------------------------------


@pytest.mark.db
async def test_начатое_обращение_оставляет_след(db_session: AsyncSession) -> None:
    """Обращение, оборванное вместе с процессом, обязано оставить запись.

    Отметка о прерванном прогоне ставится записям без отметки завершения
    (FR-041), а исход заводился после обращения — и ровно тот случай, ради
    которого отметка существует, ею и не покрывался (FR-052).
    """
    repository = MarketDataRepository(db_session)
    seen: list[str] = []

    async def action() -> int:
        runs = await _runs_of(db_session, "run-trace")
        seen.extend(f"{row.source_id}:{row.status}" for row in runs)
        return 1

    await ingest.run_source(repository, "run-trace", "equity_d1", SESSION, action)
    await db_session.commit()

    assert seen == ["equity_d1:running"]
    done = await _runs_of(db_session, "run-trace")
    assert [row.status for row in done] == [ingest.STATUS_OK]
    assert done[0].finished_at is not None


# --- FR-048: опознание по ISIN не зависит от выбора источников ---------------


class IsinCountingIss(CountingIss):
    """Биржа, считающая обращения за устойчивыми идентификаторами."""

    def __init__(self, sessions: list[dt.date] | None = None) -> None:
        super().__init__(sessions)
        self.isin_calls = 0

    async def fetch_equity_isins(self) -> dict[str, str]:
        self.isin_calls += 1
        return {"SBER": "RU0009029540"}


@pytest.mark.db
async def test_ручной_сбор_котировок_опознаёт_бумаги(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Сквозной случай ревью: выбраны одни котировки.

    Опознание привязали к сверке связей, а та выполняется только когда выбраны
    позиции: ручной сбор котировок заводил переименованной бумаге вторую
    сущность, не спросив ISIN ни разу (FR-048).
    """
    from financial_ai.market_data import groups

    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await db_session.commit()

    iss = IsinCountingIss([SESSION])
    await ingest.catch_up(
        db_session,
        settings,
        SESSION,
        sessions=[SESSION],
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        source_ids=groups.source_ids_for((groups.BY_ID[groups.GroupId.QUOTES],)),
    )

    assert iss.isin_calls == 1


@pytest.mark.db
async def test_опознание_идёт_один_раз_на_прогон(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Обратная форма: ответ один на всё окно, и спрашивать его на сессию незачем."""
    from financial_ai.market_data import groups

    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([EARLIER, SESSION])
    await db_session.commit()

    iss = IsinCountingIss([EARLIER, SESSION])
    await ingest.catch_up(
        db_session,
        settings,
        SESSION,
        sessions=[EARLIER, SESSION],
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        source_ids=groups.source_ids_for((groups.BY_ID[groups.GroupId.QUOTES],)),
    )

    assert iss.isin_calls == 1


# --- FR-050: журнал не считает остановленную сессию собранной ----------------


@pytest.mark.db
async def test_журнал_не_называет_остановленную_сессию_собранной(
    db_session: AsyncSession,
) -> None:
    """Учитывались только неудачи, и остановленный прогон выглядел завершённым.

    «Собрана 1 сессия» при трёх спрошенных бумагах из ста двадцати — ровно то
    утверждение, которого исход «прервано» и должен был не допустить (FR-050).
    """
    from financial_ai.market_data import journal

    repository = MarketDataRepository(db_session)
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-mixed",
        source_id="equity_d1",
        status=ingest.STATUS_OK,
        started_at=moment,
        finished_at=moment,
        session_date=SESSION,
    )
    await repository.record_run(
        run_id="run-mixed",
        source_id=positions.SOURCE_ID,
        status=ingest.STATUS_STOPPED,
        started_at=moment,
        finished_at=moment,
        session_date=SESSION,
        failure_reason="спрошено 3 бумаг из 120",
    )
    await db_session.commit()

    runs = await journal.recent_runs(db_session)

    assert [run.collected for run in runs] == [0]
    assert [run.failed for run in runs] == [1]


@pytest.mark.db
async def test_журнал_по_прежнему_считает_собранную_сессию_собранной(
    db_session: AsyncSession,
) -> None:
    """Обратная форма: прерванный исход не должен обесценить настоящий успех."""
    from financial_ai.market_data import journal

    repository = MarketDataRepository(db_session)
    moment = dt.datetime.now(dt.UTC)
    for source_id in ("equity_d1", positions.SOURCE_ID):
        await repository.record_run(
            run_id="run-ok",
            source_id=source_id,
            status=ingest.STATUS_OK,
            started_at=moment,
            finished_at=moment,
            session_date=SESSION,
        )
    await db_session.commit()

    runs = await journal.recent_runs(db_session)

    assert [run.collected for run in runs] == [1]
    assert [run.status for run in runs] == [journal.STATUS_FINISHED]


# --- FR-048: имя действует на всё окно прогона -------------------------------


class RenamingIss(CountingIss):
    """Биржа, у которой бумага уже переименована: ISIN тот же, тикер новый."""

    def __init__(self, sessions: list[dt.date], ticker: str, isin: str) -> None:
        super().__init__(sessions)
        self.ticker = ticker
        self.isin = isin

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        if "OPEN" not in columns:
            return []
        self.quote_calls.append(session_date)
        return [_quote(self.ticker, dt.date.fromisoformat(session_date))]

    async def fetch_equity_isins(self) -> dict[str, str]:
        return {self.ticker: self.isin}


@pytest.mark.db
async def test_переименование_не_рвёт_ряд_внутри_одного_прогона(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Сквозной случай ревью: догон двух дней после переименования.

    Опознание, датированное последним днём окна, рвало ряд внутри ОДНОГО
    прогона: котировки за первый день ложились в одну сущность, за второй — в
    другую. Имя — не связь: два имени одной сущности не могут значить разные
    бумаги в разные дни одного окна (FR-048).
    """
    from financial_ai.market_data import groups

    isin = "RU000MULT001"
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([EARLIER, SESSION])
    await repository.upsert_asset("EQ_AST_MULTOLD", "MULTOLD", EARLIER)
    await repository.update_isins({"EQ_AST_MULTOLD": isin})
    await db_session.commit()

    await ingest.catch_up(
        db_session,
        settings,
        SESSION,
        sessions=[EARLIER, SESSION],
        client=RenamingIss([EARLIER, SESSION], "MULTNEW", isin),
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        source_ids=groups.source_ids_for((groups.BY_ID[groups.GroupId.QUOTES],)),
    )
    await db_session.commit()

    bars = await repository.daily_bars_for_window([EARLIER, SESSION])
    assert len(bars) == 2
    assert {bar.asset_id for bar in bars} == {"EQ_AST_MULTOLD"}


# --- FR-049: связь датируется днём обращения ---------------------------------


class NewContractIss(CountingIss):
    """Биржа, у которой у бумаги появилось семейство контрактов.

    Считает обращения за списком серий: цена сверки связей должна зависеть от
    числа прогонов, а не от числа сессий в прогоне.
    """

    def __init__(self, sessions: list[dt.date] | None = None) -> None:
        super().__init__(sessions)
        self.series_calls = 0

    async def fetch_futures_series(self) -> list[dict[str, object]]:
        self.series_calls += 1
        return [{"underlying_asset": "SBER", "asset_code": "SBRF", "secid": "SBRF-12.26"}]

    async def fetch_futures_open_interest(self) -> dict[str, int]:
        return {"SBRF": 1000}

    async def fetch_emitter_id(self, secid: str) -> str | None:
        return "1"


@pytest.mark.db
async def test_связь_датируется_днём_обращения_а_не_концом_окна(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Сквозной случай ревью: догон исторического окна, контракт появился сегодня.

    Перевёрнутого интервала не возникало — дата окна позже прежней связи, — а
    история всё равно искажалась: сегодняшний контракт объявлялся действующим
    с последнего дня окна. Окно говорит о том, что собирают; источник говорит
    о сегодня (FR-049).
    """
    repository = MarketDataRepository(db_session)
    window = [EARLIER, SESSION]
    today = SESSION + dt.timedelta(days=21)
    await repository.add_trading_sessions([*window, today])
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSION)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSION)
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=day,
                open=Decimal("1"),
                high=None,
                low=None,
                close=Decimal("1"),
                volume=None,
            )
            for day in (*window, today)
        ]
    )
    await db_session.commit()

    await ingest.catch_up(
        db_session,
        settings,
        SESSION,
        sessions=window,
        client=NewContractIss(window),
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
    )
    await db_session.commit()

    history = await repository.link_history("EQ_AST_SBER")
    assert [link.valid_from for link in history] == [today]
    # И окну связь не приписана: источник о нём ничего не говорил.
    assert await repository.active_links_on(SESSION) == {}


# --- FR-050: неспрошенный источник попадает в журнал -------------------------


@pytest.mark.db
async def test_неспрошенный_источник_остаётся_в_журнале(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Состояние исчезает вместе с процессом, журнал — нет.

    Пока исход «не спрошен» жил только в памяти, остановленный прогон и в
    журнале выглядел завершённым (FR-050).
    """
    from financial_ai.market_data import journal

    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await db_session.commit()

    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    result = await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=CountingIss([SESSION]),
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        should_stop=should_stop,
    )
    await db_session.commit()

    assert result.interrupted
    runs = await _runs_of(db_session, result.run_id)
    assert any(row.status == ingest.STATUS_STOPPED for row in runs)

    summaries = await journal.recent_runs(db_session)
    assert [run.collected for run in summaries] == [0]


# --- FR-054: недоступная дата не называется ----------------------------------


@pytest.mark.db
async def test_дата_следующего_сбора_не_выдумывается(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Сквозной случай ревью: все сессии исчерпали попытки.

    Реальный план пуст, а сводка подставляла последнюю календарную сессию —
    то есть обещала сбор, которого не будет (FR-054).
    """
    from financial_ai.market_data import coverage

    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSION)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSION)
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=SESSION,
                open=Decimal("1"),
                high=None,
                low=None,
                close=Decimal("1"),
                volume=None,
            )
        ]
    )

    # Попытки исчерпаны: сбор такую сессию не возьмёт.
    moment = dt.datetime.now(dt.UTC)
    for attempt in range(settings.market_data_session_max_attempts + 1):
        await repository.record_run(
            run_id=f"spent-{attempt}",
            source_id="equity_agg",
            status="failed",
            started_at=moment - dt.timedelta(hours=attempt + 1),
            finished_at=moment - dt.timedelta(hours=attempt + 1),
            session_date=SESSION,
            failure_reason="биржа не ответила",
        )
    await db_session.commit()

    report = await coverage.build_report(db_session, settings, SESSION)

    assert report["next_session"] is None
    # И названа ПРИЧИНА: пустая дата иначе читается как «соберём по
    # расписанию», то есть как обещание там, где обещания нет.
    assert report["next_session_blocked"] is True


# --- FR-048: имя расширяется назад, а не дополняется строкой ------------------


@pytest.mark.db
async def test_опознание_на_каждую_сессию_не_плодит_интервалов(
    db_session: AsyncSession,
) -> None:
    """Прогон идёт от свежих сессий к старым и опознаёт бумаги на каждую.

    Каждый раз более ранней датой — и десять сессий по две бумаги давали
    двадцать интервалов, все действующие одновременно. На стенде это 506 бумаг
    на 314 сессий. Ровно этот рост уже чинила миграция 0012, и правило «писать,
    только когда имя меняется» его не остановило: имя, действующее с более
    позднего дня, на более раннем не действует (FR-048).
    """
    from sqlalchemy import func, select

    from financial_ai.market_data import links
    from financial_ai.market_data.models import AssetAlias

    class Iss:
        async def fetch_equity_isins(self) -> dict[str, str]:
            return {"SBER": "RU0009029540", "GAZP": "RU0007661625"}

    repository = MarketDataRepository(db_session)
    for offset in range(10):
        await links.sync_aliases(repository, Iss(), SESSION - dt.timedelta(days=offset))  # type: ignore[arg-type]
    await db_session.commit()

    rows = await db_session.scalar(select(func.count()).select_from(AssetAlias))

    assert rows == 2


@pytest.mark.db
async def test_имя_действует_на_самой_ранней_сессии_прогона(
    db_session: AsyncSession,
) -> None:
    """Обратная форма: экономия строк не должна стоить покрытия.

    Расширение назад и есть то, что мы узнали: имя указывает на сущность, и
    если оно действует с 18-го, то за 17-е оно указывает на неё же.
    """
    from financial_ai.market_data import links

    class Iss:
        async def fetch_equity_isins(self) -> dict[str, str]:
            return {"SBER": "RU0009029540"}

    repository = MarketDataRepository(db_session)
    earliest = SESSION - dt.timedelta(days=9)
    for offset in range(10):
        await links.sync_aliases(repository, Iss(), SESSION - dt.timedelta(days=offset))  # type: ignore[arg-type]
    await db_session.commit()

    assert await repository.aliases_on(earliest) == {"SBER": "EQ_AST_SBER"}
    assert await repository.aliases_on(SESSION) == {"SBER": "EQ_AST_SBER"}


# --- FR-049: связи не сверяются на каждую сессию ------------------------------


@pytest.mark.db
async def test_связи_сверяются_раз_на_прогон_и_в_ежедневном_цикле(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Ежедневный цикл идёт от свежих сессий к старым и сверял связи на каждую.

    Для всех сессий, кроме первой, сверка заведомо ничего не откроет — запрет
    датировать задним числом её и отвергнет, — а стоит она трёх обращений к
    бирже на сессию (FR-049).
    """
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([EARLIER, SESSION])
    await db_session.commit()

    iss = NewContractIss([EARLIER, SESSION])
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
    )
    after_first = iss.series_calls
    assert after_first == 1

    await ingest.ingest_session(
        db_session,
        settings,
        EARLIER,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
    )

    assert iss.series_calls == after_first


# --- FR-049: посессионный путь тоже датирует днём обращения ------------------


@pytest.mark.db
async def test_добор_пропуска_не_датирует_связь_датой_сессии(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Сквозной случай ревью: календарь до 18-го, добирается пропуск за 14-е.

    Сегодняшний контракт записывался действующим с 14-го. Правило чинили в
    ручном догоне, а посессионный путь оставили на дате сессии (FR-049).
    """
    repository = MarketDataRepository(db_session)
    today = SESSION + dt.timedelta(days=21)
    await repository.add_trading_sessions([SESSION, today])
    await db_session.commit()

    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=NewContractIss([SESSION, today]),
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
    )
    await db_session.commit()

    history = await repository.link_history("EQ_AST_SBER")
    assert [link.valid_from for link in history] == [today]
    assert await repository.active_links_on(SESSION) == {}


# --- FR-054: ожидание повтора — не исчерпание попыток ------------------------


@pytest.mark.db
async def test_ожидание_повтора_не_зовёт_человека(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Сквозной случай ревью: одна попытка из трёх, выдержка ещё идёт.

    Сессию, ждущую повтора, сбор возьмёт САМ — просто позже. Раздел же
    сообщал «нужен ручной сбор», то есть звал вмешаться там, где вмешиваться
    не нужно (FR-054).
    """
    from financial_ai.market_data import coverage

    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSION)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSION)
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=SESSION,
                open=Decimal("1"),
                high=None,
                low=None,
                close=Decimal("1"),
                volume=None,
            )
        ]
    )

    # Одна попытка из трёх, и она была только что: выдержка ещё идёт.
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="just-tried",
        source_id="equity_d1",
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=SESSION,
        failure_reason="биржа не ответила",
    )
    await db_session.commit()

    report = await coverage.build_report(db_session, settings, SESSION)

    assert report["next_session"] == SESSION.isoformat()
    assert report["next_session_blocked"] is False


@pytest.mark.db
async def test_исчерпание_попыток_человека_всё_же_зовёт(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Обратная форма: различие не должно стереть и второй случай.

    Сессию, исчерпавшую предел попыток, не возьмёт никто, пока человек не
    вмешается, — и об этом сказать обязаны.
    """
    from financial_ai.market_data import coverage

    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSION)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSION)
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=SESSION,
                open=Decimal("1"),
                high=None,
                low=None,
                close=Decimal("1"),
                volume=None,
            )
        ]
    )

    moment = dt.datetime.now(dt.UTC)
    for attempt in range(settings.market_data_session_max_attempts + 1):
        await repository.record_run(
            run_id=f"spent-{attempt}",
            source_id="equity_d1",
            status="failed",
            started_at=moment - dt.timedelta(hours=attempt + 1),
            finished_at=moment - dt.timedelta(hours=attempt + 1),
            session_date=SESSION,
            failure_reason="биржа не ответила",
        )
    await db_session.commit()

    report = await coverage.build_report(db_session, settings, SESSION)

    assert report["next_session"] is None
    assert report["next_session_blocked"] is True


# --- FR-041: обычная неудача прерванностью не становится ---------------------


@pytest.mark.db
async def test_обычная_неудача_прерванным_прогоном_не_считается(
    db_session: AsyncSession,
) -> None:
    """Обратная форма: исход «прерван» отличает оборванный прогон от неудачного."""
    from financial_ai.market_data import journal

    repository = MarketDataRepository(db_session)
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-broken",
        source_id="equity_d1",
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=SESSION,
        failure_reason="биржа не ответила",
    )
    await db_session.commit()

    runs = await journal.recent_runs(db_session)

    assert [run.status for run in runs] == [journal.STATUS_FAILED]


# --- FR-048: справочники ключуются сущностью, а не именем --------------------


class ReferenceIss:
    """Биржа, знающая бумагу под НОВЫМ именем после переименования."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker

    async def fetch_index_titles(self) -> dict[str, str]:
        return {"MOEXFN": "Финансы"}

    async def fetch_index_analytics(
        self, index_id: str, session_date: str | None = None
    ) -> list[dict[str, object]]:
        # Отрасль — тот индекс, где вес бумаги наибольший: бумага входит
        # ровно в один, иначе выбор решал бы порядок перечня.
        if index_id != "MOEXFN":
            return []
        return [{"ticker": self.ticker, "weight": "10", "tradedate": SESSION.isoformat()}]

    async def fetch_equity_lot_sizes(self) -> dict[str, int]:
        return {self.ticker: 100}

    async def fetch_equity_isins(self) -> dict[str, str]:
        return {self.ticker: "RU000MULT001"}


async def _renamed(repository: MarketDataRepository) -> None:
    """Бумага, переименованная из MULTOLD в MULTNEW: сущность прежняя."""
    await repository.upsert_asset("EQ_AST_MULTOLD", "MULTOLD", SESSION)
    await repository.update_lot_sizes({"EQ_AST_MULTOLD": 10})
    await repository.upsert_alias("MULTNEW", "EQ_AST_MULTOLD", SESSION)


@pytest.mark.db
async def test_отрасль_ложится_на_настоящую_сущность(db_session: AsyncSession) -> None:
    """Справочник пишет строку по любому ключу и молчит.

    У переименованной бумаги отрасль уходила на сущность, которой нет, а
    настоящая оставалась без неё (FR-048).
    """
    repository = MarketDataRepository(db_session)
    await _renamed(repository)
    await db_session.commit()

    await reference.sync_sectors(ReferenceIss("MULTNEW"), repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert await repository.sectors() == {"EQ_AST_MULTOLD": "Финансы"}


@pytest.mark.db
async def test_размер_лота_переименованной_бумаги_обновляется(
    db_session: AsyncSession,
) -> None:
    """Обновление по несуществующему ключу не затрагивает строк и молчит.

    Размер лота переименованной бумаги переставал обновляться вовсе, и
    заметить это было нечем (FR-048).
    """
    from sqlalchemy import select

    from financial_ai.market_data.models import MarketAsset

    repository = MarketDataRepository(db_session)
    await _renamed(repository)
    await db_session.commit()

    await securities.sync_lot_sizes(ReferenceIss("MULTNEW"), repository)  # type: ignore[arg-type]
    await db_session.commit()

    lots = (await db_session.execute(select(MarketAsset.asset_id, MarketAsset.lot_size))).all()
    assert list(lots) == [("EQ_AST_MULTOLD", 100)]


@pytest.mark.db
async def test_бумага_без_переименования_ключуется_как_прежде(
    db_session: AsyncSession,
) -> None:
    """Обратная форма: разрешение псевдонимов не должно трогать обычную бумагу."""
    repository = MarketDataRepository(db_session)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSION)
    await db_session.commit()

    await reference.sync_sectors(ReferenceIss("SBER"), repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert await repository.sectors() == {"EQ_AST_SBER": "Финансы"}


def test_ряд_весов_составляется_из_канонического_имени() -> None:
    """Иначе переименование начинает второй ряд весов, а прежний обрывается."""
    rows = [{"ticker": "MULTNEW", "weight": "10", "tradedate": SESSION.isoformat()}]

    series = reference.rows_to_weights(rows, SESSION, "IMOEX", {"MULTNEW": "MULTOLD"})

    assert list(series) == ["IDX_WEIGHT_IMOEX_MULTOLD"]


def test_ряд_весов_без_переименования_не_меняется() -> None:
    """Обратная форма: имя ряда обычной бумаги остаётся прежним."""
    rows = [{"ticker": "SBER", "weight": "10", "tradedate": SESSION.isoformat()}]

    series = reference.rows_to_weights(rows, SESSION, "IMOEX", {"MULTNEW": "MULTOLD"})

    assert list(series) == ["IDX_WEIGHT_IMOEX_SBER"]


@pytest.mark.db
async def test_расширение_имени_останавливается_у_чужого_интервала(
    db_session: AsyncSession,
) -> None:
    """Тикер может смениться владельцем — ради этого имена и ведутся датами.

    Расширять действующий интервал глубже конца предыдущего значило бы
    утверждать, что имя указывало на новую сущность тогда, когда оно указывало
    на старую. А технически — нарушить ключ «имя и начало действия» и уронить
    прогон целиком (FR-048).
    """
    repository = MarketDataRepository(db_session)
    first, handover, window_start = (
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 11),
        dt.date(2026, 9, 1),
    )

    await repository.upsert_alias("T", "EQ_AST_A", first)
    await repository.close_alias("T", handover - dt.timedelta(days=1))
    await repository.upsert_alias("T", "EQ_AST_B", handover)
    await db_session.commit()

    # Догон окна, начинающегося раньше передачи имени.
    await repository.upsert_alias("T", "EQ_AST_B", window_start)
    await db_session.commit()

    assert await repository.aliases_on(dt.date(2026, 9, 5)) == {"T": "EQ_AST_A"}
    assert await repository.aliases_on(dt.date(2026, 9, 15)) == {"T": "EQ_AST_B"}


@pytest.mark.db
async def test_расширение_имени_без_чужого_интервала_проходит(
    db_session: AsyncSession,
) -> None:
    """Обратная форма: граница не должна отменить само расширение."""
    repository = MarketDataRepository(db_session)
    later, earlier = dt.date(2026, 9, 11), dt.date(2026, 9, 1)

    await repository.upsert_alias("T", "EQ_AST_A", later)
    await repository.upsert_alias("T", "EQ_AST_A", earlier)
    await db_session.commit()

    assert await repository.aliases_on(dt.date(2026, 9, 5)) == {"T": "EQ_AST_A"}


# --- FR-058e: собранное этим прогоном заново не спрашивается ------------------


@pytest.mark.db
async def test_прогон_не_переспрашивает_собранное_им_же(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Сквозной случай: прогон вернулся к недобранной сессии.

    На стенде 2026-09-19 после остановки и продолжения котировки за одну
    сессию спрашивались трижды, все три раза успешно. Это прямо противоречит
    правилу «обращения, заведомо не приносящие данных, не выполняются»
    (FR-058e, FR-022).
    """
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await db_session.commit()

    iss = CountingIss([SESSION])
    run_id = "один-прогон"

    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        run_id=run_id,
    )
    after_first = list(iss.quote_calls)
    assert after_first == [SESSION.isoformat()]

    # Тот же прогон возвращается к той же сессии.
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        run_id=run_id,
    )

    assert iss.quote_calls == after_first


@pytest.mark.db
async def test_закрытый_источник_не_спрашивается_и_следующим_прогоном(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Сессия попадает в план из-за НЕДОСТАЮЩЕГО источника.

    Спрашивать заодно собранные значит делать обращения, заведомо не
    приносящие данных: на стенде котировки числились собранными по всем 314
    сессиям и всё равно запрашивались при каждом заходе в сессию (FR-058k).

    Цена правила названа в нём прямо: переиздание бара биржей за уже закрытую
    сессию сбор сам не подхватит. Что сам источник поправку применяет,
    проверяется отдельно — в `test_ingest_cycle`.
    """
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await db_session.commit()

    iss = CountingIss([SESSION])
    for _ in range(2):
        await ingest.ingest_session(
            db_session,
            settings,
            SESSION,
            client=iss,
            cbr_client=cbr_client,
            positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        )

    assert iss.quote_calls == [SESSION.isoformat()]


# --- FR-058f: исход назван числом и при успехе -------------------------------


def test_собранный_источник_назван_числом() -> None:
    """Подпись собранного источника отличает идущую работу от замершей."""
    from financial_ai.market_data import plan

    assert plan.describe("equity_d1", 243) == "243 бумаги"
    assert plan.describe("equity_d1", 1) == "1 бумага"
    assert plan.describe("futures_positions", 11) == "11 фьючерсов"
    assert plan.describe("global_series", 9) == "9 рядов"


def test_там_где_число_ничего_не_добавляет_подписи_нет() -> None:
    """Обратная форма: у Brent ряд один, у календаря счёт не о том.

    Подпись, которой то есть, то нет без причины, читается как неисправность.
    """
    from financial_ai.market_data import plan

    assert plan.describe("brent", 1) is None
    assert plan.describe("trading_calendar", 300) is None
    assert plan.describe("equity_d1", 0) is None


# --- FR-058i: источник по инструментам показывает свой ход -------------------


@pytest.mark.db
async def test_позиции_показывают_сколько_инструментов_пройдено(
    db_session: AsyncSession,
) -> None:
    """Источник идёт минутами и десятками обращений.

    Всё это время на экране стояло одно слово «идёт»: долгий источник
    неотличим от зависшего — ровно та беда, ради которой лента и заведена
    (FR-058i, FR-003).
    """
    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await _seed(repository, "GAZP", "GAZR", traded=True)
    await db_session.commit()

    client = FakePositionsClient(
        available={("SBRF", SESSION): FakeSnapshot(), ("GAZR", SESSION): FakeSnapshot()}
    )
    seen: list[tuple[int, int]] = []

    await positions.sync_positions(  # type: ignore[arg-type]
        client,
        repository,
        SESSION,
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1, 2), (2, 2)]


@pytest.mark.db
async def test_ход_считается_по_тем_кого_спрашивают(db_session: AsyncSession) -> None:
    """Обратная форма: в знаменателе те, кого спрашивают, а не все подряд.

    Ушедшая с торгов бумага в круг не входит (FR-053), и обещать её в счёте
    значило бы обещать обращение, которого не будет.
    """
    repository = MarketDataRepository(db_session)
    await _seed(repository, "SBER", "SBRF", traded=True)
    await _seed(repository, "DOMRF", "DOMR", traded=False)
    await db_session.commit()

    client = FakePositionsClient(available={("SBRF", SESSION): FakeSnapshot()})
    seen: list[tuple[int, int]] = []

    await positions.sync_positions(  # type: ignore[arg-type]
        client,
        repository,
        SESSION,
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1, 1)]


@pytest.mark.db
async def test_после_паузы_позиции_не_спрашивают_собранное(
    db_session: AsyncSession,
) -> None:
    """Единица обращения здесь — «инструмент и дата», и пауза её не отменяет.

    Собранное до остановки записано; продолжение обязано спросить только
    остаток, а не пройти круг заново (FR-024c).
    """
    repository = MarketDataRepository(db_session)
    for ticker, contract in (("SBER", "SBRF"), ("GAZP", "GAZR"), ("LKOH", "LKOH")):
        await _seed(repository, ticker, contract, traded=True)
    await db_session.commit()

    available = {(code, SESSION): FakeSnapshot() for code in ("SBRF", "GAZR", "LKOH")}
    first = FakePositionsClient(available=available)
    seen = {"n": 0}

    def should_stop() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    with pytest.raises(SourceStoppedError):
        await positions.sync_positions(  # type: ignore[arg-type]
            first, repository, SESSION, should_stop=should_stop
        )
    await db_session.commit()

    asked_first = [code for code, _ in first.calls]
    assert len(asked_first) == 1

    second = FakePositionsClient(available=available)
    await positions.sync_positions(second, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    asked_second = [code for code, _ in second.calls]
    assert asked_first[0] not in asked_second
    assert len(asked_second) == 2


@pytest.mark.db
async def test_счётчик_не_шагает_по_собранным(db_session: AsyncSession) -> None:
    """Счёт идёт по ОБРАЩЕНИЯМ, а не по позициям в списке.

    Собранные пары пропускаются, и счётчик по списку пробегал круг целиком,
    не спросив никого: после паузы это читается как «собирает заново», хотя
    собранное как раз не трогается (FR-058i, FR-024c).
    """
    repository = MarketDataRepository(db_session)
    for ticker, contract in (("SBER", "SBRF"), ("GAZP", "GAZR"), ("LKOH", "LKOH")):
        await _seed(repository, ticker, contract, traded=True)
    await repository.upsert_positions(
        [
            PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=SESSION,
                contract_code="SBRF",
                fiz_long=Decimal("1"),
                fiz_short=None,
                jur_long=None,
                jur_short=None,
            )
        ]
    )
    await db_session.commit()

    available = {(code, SESSION): FakeSnapshot() for code in ("GAZR", "LKOH")}
    client = FakePositionsClient(available=available)
    seen: list[tuple[int, int]] = []

    await positions.sync_positions(  # type: ignore[arg-type]
        client,
        repository,
        SESSION,
        on_progress=lambda done, total: seen.append((done, total)),
    )

    # Спросить предстоит двоих из трёх — собранного в счёт не берём.
    assert seen == [(1, 2), (2, 2)]


# --- FR-058l: известное объявляется в начале сессии --------------------------


@pytest.mark.db
async def test_суточный_справочник_убирается_из_плана_сразу(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """До первого обращения известно, что сегодня его не спрашивают.

    Объявленное по ходу очереди показывало ожидающими строки, которых в работе
    нет, и лента «плясала», пока прогон доходил до каждой (FR-058l).
    """
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([EARLIER, SESSION])
    await db_session.commit()

    iss = CountingIss([EARLIER, SESSION])
    # Первый прогон спрашивает справочники — суточный гейт закрывается.
    await ingest.ingest_session(
        db_session,
        settings,
        EARLIER,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
    )

    seen: list[tuple[str, str]] = []
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        on_source=lambda source_id, status, outcome: seen.append((source_id, status)),
    )

    # Оба справочника объявлены ДО первого обращения к бирже.
    omitted = [i for i, (_, status) in enumerate(seen) if status == ingest.STATUS_OMITTED]
    first_request = next(i for i, (_, status) in enumerate(seen) if status == "running")
    assert len(omitted) == 2
    assert max(omitted) < first_request


@pytest.mark.db
async def test_закрытый_источник_объявляется_собранным_сразу(
    db_session: AsyncSession, settings: Settings, cbr_client: httpx.AsyncClient
) -> None:
    """Обратная форма: закрытая строка не должна висеть ожидающей до своей очереди."""
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([SESSION])
    await db_session.commit()

    iss = CountingIss([SESSION])
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
    )

    seen: list[tuple[str, str]] = []
    await ingest.ingest_session(
        db_session,
        settings,
        SESSION,
        client=iss,
        cbr_client=cbr_client,
        positions_client=FakePositionsClient(),  # type: ignore[arg-type]
        on_source=lambda source_id, status, outcome: seen.append((source_id, status)),
    )

    assert ("equity_d1", ingest.STATUS_OK) in seen
    # И ни одного обращения к нему: он закрыт за эту сессию.
    assert iss.quote_calls == [SESSION.isoformat()]
