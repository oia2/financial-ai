"""Тесты источника позиций по фьючерсам.

Образец таблицы — строки **настоящего ответа** биржи
(`open-positions-csv.aspx?d=20260922&t=1`, снят 2026-09-23), а не придуманы:
все дефекты этого источника были расхождением с настоящим ответом, а тесты на
подделках при этом проходили.

Живая сверка — отдельной командой вне гейта: `cli verify-positions` (FR-025).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest, links
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import positions
from financial_ai.market_data.sources import positions_client as module
from financial_ai.market_data.sources.positions_client import (
    PositionFetchKind,
    PositionFetchResult,
    PositionsClient,
    PositionsSourceError,
    parse_day_table,
)
from tests.market_data.conftest import FakePositionsClient, FakeSnapshot

SESSION = dt.date(2026, 9, 22)
OTHER = dt.date(2026, 9, 21)
WEEKEND = dt.date(2026, 9, 20)


class TypedPositionsClient:
    """Источник с явными исходами пары для проверок оркестрации."""

    def __init__(self, results: dict[tuple[str, dt.date], PositionFetchResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, dt.date]] = []

    async def fetch(self, contract_code: str, day: dt.date) -> PositionFetchResult:
        self.calls.append((contract_code, day))
        return self.results[(contract_code, day)]


HEADER = (
    "moment,isin,name,contract_type,iz_fiz,clients_in_long,clients_in_short,"
    "long_position,short_position,change_prev_week_long_abs,change_prev_week_short_abs,"
    "change_prev_week_long_perc,change_prev_week_short_perc,"
)


def _table(day: dt.date = SESSION, rows: list[str] | None = None) -> str:
    """Таблица за дату: строки живого ответа 22.09.2026 с подставленной датой."""
    lines = rows if rows is not None else LIVE_ROWS
    return "\ufeff" + "\n".join([HEADER, *(line.format(day=day) for line in lines)]) + "\n"


# Строки живого ответа за 22.09.2026. Опционы SBRF стоят рядом с фьючерсами и
# в наблюдение не входят; у LENT юридические лица без коротких позиций.
LIVE_ROWS = [
    "{day},SBRF,Опцион SBRF,C,,1879.0,7.0000,1677348,1705149,5382.0,7755.0,0.00322,0.00457,",
    "{day},SBRF,Опцион SBRF,C,1.0000,675.00,127.00,651163,623362,50438,48065,0.08396,0.08355,",
    "{day},SBRF,Фьючерс SBRF,F,,38.000,52.000,29854,97640,3177.0,1822.0,0.11909,0.01902,",
    "{day},SBRF,Фьючерс SBRF,F,1.0000,2719.0,1334.0,114175,46389,1754.0,3109.0,0.01560,0.07183,",
    "{day},LENT,Фьючерс LENT,F,,1.0000,,1390.0,,241.00,,0.20975,,",
    "{day},LENT,Фьючерс LENT,F,1.0000,145.00,107.00,4042.0,5432.0,-150.00,91.000,-0.03578,0.01704,",
]

# Серии семейств, включая истёкшие (`show_expired=1`): LENT торгуется с 16.06.2026.
SERIES = {
    "LENT": [["LNZ6", "LENT-12.26", "2026-06-16", "2026-12-18", "LENT", "LENT", 1]],
    # Спред и служебная серия начались на день раньше самого фьючерса.
    "FIXR": [
        ["FIU6FIZ6", "FIXR-9.26-12.26", "2026-08-17", "2026-09-18", "FIXR", "FIXR", 0],
        ["FIXR_CLT", "FIXR_CLT", "2026-08-17", "2100-01-01", "FIXR", "FIXR", 0],
        ["FIU6", "FIXR-9.26", "2026-08-18", "2026-09-18", "FIXR", "FIXR", 0],
    ],
    "SBRF": [["SRZ6", "SBRF-12.26", "2025-12-04", "2026-12-18", "SBRF", "SBER", 1]],
}


def _series_body(family: str) -> str:
    import json

    return json.dumps(
        {
            "series": {
                "columns": [
                    "secid",
                    "name",
                    "start_date",
                    "expiration_date",
                    "asset_code",
                    "underlying_asset",
                    "is_traded",
                ],
                "data": SERIES.get(family, []),
            }
        }
    )


# --- разбор таблицы (FR-053a) ---------------------------------------------------


def test_futures_rows_are_split_into_fiz_and_jur() -> None:
    """`iz_fiz = 1` — физические лица, пусто — юридические; опционы не берутся."""
    table = parse_day_table(_table(), SESSION)

    sber = table.families["SBRF"]
    assert (sber.fiz_long, sber.fiz_short) == (Decimal("114175"), Decimal("46389"))
    assert (sber.jur_long, sber.jur_short) == (Decimal("29854"), Decimal("97640"))


def test_missing_side_value_stays_none() -> None:
    """Пустая ячейка — законный пропуск, а не ноль."""
    lent = parse_day_table(_table(), SESSION).families["LENT"]
    assert lent.jur_long == Decimal("1390.0")
    assert lent.jur_short is None


def test_header_only_means_not_published() -> None:
    """Биржа отдаёт только заголовок за день без данных: это неизвестность."""
    table = parse_day_table(_table(rows=[]), WEEKEND)
    assert table.published is False
    assert table.families == {}


def test_foreign_date_is_a_contract_violation() -> None:
    """Строка за другой день — не наблюдение о запрошенной сессии."""
    with pytest.raises(PositionsSourceError, match="относится"):
        parse_day_table(_table(day=OTHER), SESSION)


def test_duplicate_family_side_is_a_contract_violation() -> None:
    rows = [*LIVE_ROWS, LIVE_ROWS[3]]
    with pytest.raises(PositionsSourceError, match="повтор"):
        parse_day_table(_table(rows=rows), SESSION)


def test_broken_number_is_not_silently_empty() -> None:
    """Нераспознанное число — нарушение контракта, а не пропуск (FR-032e)."""
    rows = [LIVE_ROWS[2].replace("29854", "н/д")]
    with pytest.raises(Exception, match="нераспознанное число"):
        parse_day_table(_table(rows=rows), SESSION)


def test_missing_column_is_a_contract_violation() -> None:
    body = "moment,isin,contract_type\n2026-09-22,SBRF,F\n"
    with pytest.raises(PositionsSourceError, match="обязательных колонок"):
        parse_day_table(body, SESSION)


class FakeIss:
    """Подделка ISS: серии срочного рынка и открытый интерес."""

    def __init__(
        self,
        series: list[dict[str, object]] | None = None,
        open_interest: dict[str, int] | None = None,
    ) -> None:
        self.series = (
            series
            if series is not None
            else [
                {"asset_code": "SBRF", "underlying_asset": "SBER"},
                {"asset_code": "SBERF", "underlying_asset": "SBER"},
                {"asset_code": "NOTKM", "underlying_asset": "NVTK"},
                {"asset_code": "CNYRUBTOM", "underlying_asset": None},
            ]
        )
        self.open_interest = (
            open_interest if open_interest is not None else {"SBRF": 1045330, "SBERF": 214996}
        )
        self.calls = 0

    async def fetch_futures_series(self) -> list[dict[str, object]]:
        self.calls += 1
        return self.series

    async def fetch_futures_open_interest(self) -> dict[str, int]:
        return self.open_interest


# Правило выбора живёт в одном месте — `market_data/links.py`. Здесь оно
# проверяется на тех же образцах, на которых проверялось прежнее второе его
# воплощение в клиенте позиций: два кода одного правила однажды разошлись бы.


async def test_contract_code_is_taken_from_iss_not_guessed() -> None:
    """Правилом код не выводится: NVTK — это NOTKM_F, а не NVTK_F."""
    mapping = await links.build_candidates(FakeIss())  # type: ignore[arg-type]

    assert mapping["NVTK"].contract_code == "NOTKM_F"
    assert "NVTK_F" not in {candidate.contract_code for candidate in mapping.values()}


async def test_open_interest_resolves_several_contracts() -> None:
    """У SBER два кода; берётся тот, где на самом деле торгуют."""
    mapping = await links.build_candidates(FakeIss())  # type: ignore[arg-type]

    assert mapping["SBER"].contract_code == "SBRF_F"


async def test_series_without_underlying_are_skipped() -> None:
    """Валютные и индексные серии базовым активом акцию не имеют."""
    mapping = await links.build_candidates(FakeIss())  # type: ignore[arg-type]

    assert set(mapping) == {"SBER", "NVTK"}


# --- обмен: одна таблица на дату, темп, повторы (FR-053a, FR-032g) -------------


class Recorder:
    """Подделка сайта биржи и ISS. Считает обращения и отдаёт заготовленное."""

    def __init__(self, tables: dict[dt.date, str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.tables = tables if tables is not None else {SESSION: _table()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("series.json"):
            return httpx.Response(200, text=_series_body(request.url.params["asset_code"]))
        day = dt.datetime.strptime(request.url.params["d"], "%Y%m%d").date()
        return httpx.Response(200, text=self.tables.get(day, _table(rows=[])))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "market_data_positions_batch_size": 2,
        "market_data_positions_batch_pause_seconds": 0.0,
        "market_data_positions_retries": 2,
        "market_data_positions_retry_backoff_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def test_one_request_per_date_serves_every_family() -> None:
    """Все семейства даты — из одной таблицы: прежде ~70 обращений на сессию."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        sber = await client.fetch("SBRF_F", SESSION)
        lent = await client.fetch("LENT_F", SESSION)

    assert sber.kind is PositionFetchKind.VALUE
    assert lent.kind is PositionFetchKind.VALUE
    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.params["d"] == "20260922"


async def test_pause_is_kept_between_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Между пачками обращений выдерживается пауза из конфигурации."""
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    days = [SESSION - dt.timedelta(days=shift) for shift in range(5)]
    recorder = Recorder({day: _table(day) for day in days})
    settings = _settings(
        market_data_positions_batch_size=2,
        market_data_positions_batch_pause_seconds=0.25,
    )
    async with PositionsClient(settings, client=recorder.client()) as client:
        for day in days:
            await client.fetch("SBRF_F", day)

    assert len(recorder.requests) == 5
    assert pauses == [0.25, 0.25]


async def test_unpublished_day_is_unknown() -> None:
    """Таблица без строк — день не опубликован, а не отсутствие позиций."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", WEEKEND)

    assert result.kind is PositionFetchKind.UNKNOWN
    assert result.reason_code == "day_not_published"


async def test_family_before_its_first_series_is_not_applicable() -> None:
    """LENT торгуется с 16.06.2026: 29.05 его законно нет (FR-032g)."""
    early = dt.date(2026, 5, 29)
    recorder = Recorder({early: _table(early, [LIVE_ROWS[2], LIVE_ROWS[3]])})
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("LENT_F", early)

    assert result.kind is PositionFetchKind.NOT_APPLICABLE
    assert result.reason_code == "contract_family_not_traded_yet"
    series = [r for r in recorder.requests if r.url.path.endswith("series.json")]
    assert series[0].url.params["show_expired"] == "1"


async def test_spread_does_not_start_the_family() -> None:
    """17.08.2026 у FIXR был только спред: позиций по фьючерсу быть не могло."""
    day = dt.date(2026, 8, 17)
    recorder = Recorder({day: _table(day, [LIVE_ROWS[2]])})
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("FIXR_F", day)

    assert result.kind is PositionFetchKind.NOT_APPLICABLE


async def test_family_missing_after_its_start_stays_unknown() -> None:
    """Семейство уже торговалось, но в таблице его нет — это не отсутствие."""
    recorder = Recorder({SESSION: _table(rows=LIVE_ROWS[4:])})
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", SESSION)

    assert result.kind is PositionFetchKind.UNKNOWN
    assert result.reason_code == "family_missing_in_day_table"


async def test_family_without_series_stays_unknown() -> None:
    """Серий семейства у биржи нет — основания для неприменимости нет."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("NEWF_F", SESSION)

    assert result.kind is PositionFetchKind.UNKNOWN


async def test_family_start_is_asked_once_per_run() -> None:
    early = dt.date(2026, 5, 29)
    recorder = Recorder({early: _table(early, [LIVE_ROWS[2]])})
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        await client.fetch("LENT_F", early)
        await client.fetch("LENT_F", early - dt.timedelta(days=1))

    series = [r for r in recorder.requests if r.url.path.endswith("series.json")]
    assert len(series) == 1


async def test_exact_date_empty_row_confirms_absence() -> None:
    rows = ["{day},SBRF,Фьючерс SBRF,F,,,,,,,,,,", "{day},SBRF,Фьючерс SBRF,F,1,,,,,,,,,"]
    recorder = Recorder({SESSION: _table(rows=rows)})
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", SESSION)

    assert result.kind is PositionFetchKind.CONFIRMED_ABSENCE
    assert result.reason_code == "exact_date_empty_position_row"


async def test_zero_positions_are_values() -> None:
    rows = ["{day},SBRF,Фьючерс SBRF,F,1.0000,1,1,0,0,,,,"]
    recorder = Recorder({SESSION: _table(rows=rows)})
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", SESSION)

    assert result.kind is PositionFetchKind.VALUE
    assert result.snapshot is not None
    assert result.snapshot.fiz_long == Decimal("0")


async def test_known_contracts_come_from_the_day_table() -> None:
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        assert await client.known_contracts(SESSION) == {"SBRF_F", "LENT_F"}


async def test_retries_are_bounded_and_reported() -> None:
    """Повторы конечны, а исчерпание — ошибка с причиной."""

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="сервис недоступен")

    client = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    async with PositionsClient(_settings(), client=client) as positions_client:
        with pytest.raises(PositionsSourceError):
            await positions_client.fetch("SBRF_F", SESSION)


# --- первая доступная дата ------------------------------------------------------


async def test_without_sessions_nothing_is_searched() -> None:
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        found = await client.first_available_date("SBRF_F", [])

        assert found is None
        assert client.searched_for_first_date("SBRF_F") is False
        assert recorder.requests == []


async def test_first_available_date_is_searched_once() -> None:
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        first = await client.first_available_date("SBRF_F", [WEEKEND, SESSION])
        spent = len(recorder.requests)
        again = await client.first_available_date("SBRF_F", [WEEKEND, SESSION])

    assert first == again == SESSION
    assert len(recorder.requests) == spent


# --- правила сбора сессии (FR-018, FR-022, FR-024b, FR-024c) -----------------
#
# Ниже — уровень источника, а не клиента: все три правила требуют знания уже
# собранного, поэтому живут в `positions.py` и проверяются на хранилище.


@pytest.mark.db
async def test_empty_response_is_a_failure_not_a_partial_success(
    db_session: AsyncSession,
) -> None:
    """Ноль значений — неуспех с причиной, а не успех «покрытие частичное».

    Ровно эта формулировка скрыла дефект: 260 пустых строк на сессию
    записывались как успех, и группа выглядела собранной.
    """
    await _seed_assets(db_session, ["SBER"])
    client = FakePositionsClient(empty=True)

    result = await positions.sync_positions(
        client,  # type: ignore[arg-type]
        MarketDataRepository(db_session),
        SESSION,
    )

    assert result.complete is False
    assert result.evidence == ()


@pytest.mark.db
async def test_value_and_unknown_pair_remain_incomplete(db_session: AsyncSession) -> None:
    """Одна полученная пара не доказывает неизвестный ответ по другой."""
    await _seed_assets(db_session, ["SBER", "GAZP"], {"SBER": "SBRF_F", "GAZP": "GAZR_F"})
    client = FakePositionsClient(available={("SBRF_F", SESSION): FakeSnapshot()})

    written = await positions.sync_positions(
        client,  # type: ignore[arg-type]
        MarketDataRepository(db_session),
        SESSION,
    )

    assert written.rows_written == 1
    assert written.complete is False
    assert [item.work_key for item in written.evidence] == ["pair:EQ_AST_SBER:SBRF_F"]


@pytest.mark.db
async def test_collected_pair_is_not_asked_again(db_session: AsyncSession) -> None:
    """FR-024c: повторный догон собранного периода не стоит ничего."""
    await _seed_assets(db_session, ["SBER"])
    repository = MarketDataRepository(db_session)
    first = FakePositionsClient()

    async def first_run() -> object:
        return await positions.sync_positions(first, repository, SESSION)  # type: ignore[arg-type]

    await ingest.run_source(repository, "first", positions.SOURCE_ID, SESSION, first_run)
    await db_session.commit()

    second = FakePositionsClient()
    written = await positions.sync_positions(
        second,  # type: ignore[arg-type]
        repository,
        SESSION,
    )

    assert first.calls == [("SBRF_F", SESSION)]
    assert second.calls == []
    assert written.rows_written == 0


@pytest.mark.db
async def test_confirmed_absence_is_proof_and_is_not_asked_again(
    db_session: AsyncSession,
) -> None:
    """Подтверждённая пустая строка закрывает ровно проверенную пару."""
    await _seed_assets(db_session, ["SBER"])
    repository = MarketDataRepository(db_session)
    first = TypedPositionsClient(
        {
            ("SBRF_F", SESSION): PositionFetchResult.confirmed_absence(
                "exact_date_empty_position_row"
            )
        }
    )

    async def first_run() -> object:
        return await positions.sync_positions(first, repository, SESSION)  # type: ignore[arg-type]

    outcome = await ingest.run_source(
        repository, "absence-first", positions.SOURCE_ID, SESSION, first_run
    )
    await db_session.commit()

    second = TypedPositionsClient({})
    result = await positions.sync_positions(second, repository, SESSION)  # type: ignore[arg-type]

    assert outcome.status == "ok"
    assert first.calls == [("SBRF_F", SESSION)]
    assert second.calls == []
    assert result.complete is True


@pytest.mark.db
async def test_share_without_a_contract_is_not_asked(db_session: AsyncSession) -> None:
    """FR-022: фьючерса на бумагу нет — обращения не выполняется."""
    await _seed_assets(db_session, ["SBER", "ABRD"])
    client = FakePositionsClient()

    await positions.sync_positions(
        client,  # type: ignore[arg-type]
        MarketDataRepository(db_session),
        SESSION,
    )

    assert [code for code, _ in client.calls] == ["SBRF_F"]


@pytest.mark.db
async def test_date_before_the_found_lower_bound_is_still_asked(
    db_session: AsyncSession,
) -> None:
    """Ненайденная нижняя граница отсутствием данных не является.

    Прежде этот тест требовал обратного: сессия раньше найденной первой
    доступной даты не спрашивалась вовсе. Найденная дата доказывает, что
    инструмент тогда УЖЕ БЫЛ, и ничего не говорит про более раннее время
    (FR-034). А сетка проб редкая, и пустые даты у позиций законны: контракт
    существует, сделок в этот день нет.

    Из-за этого правила догон за 2026-08-10…14 пропускал 69 активов из 69 при
    нулевых обращениях. Теперь нужная дата проверяется обращением: незнание
    основанием не спрашивать не является (T206, FR-032).
    """
    await _seed_assets(db_session, ["SBER"])
    client = FakePositionsClient(first_available={"SBRF_F": SESSION + dt.timedelta(days=7)})

    written = await positions.sync_positions(
        client,  # type: ignore[arg-type]
        MarketDataRepository(db_session),
        SESSION,
        sessions=[SESSION],
    )

    # Обращение выполнено — и принесло значения. Ровно их прежнее правило и
    # теряло, не спросив ни разу.
    assert client.calls == [("SBRF_F", SESSION)]
    assert written.rows_written == 1


@pytest.mark.db
async def test_marker_from_data_saves_the_search_for_later_sessions(
    db_session: AsyncSession,
) -> None:
    """Собранная сессия доказывает, что инструмент тогда существовал.

    Для сессий не старше её поиск не нужен: ответ уже известен из данных.
    """
    later = SESSION + dt.timedelta(days=1)
    await _seed_assets(db_session, ["SBER"], sessions=[SESSION, later])
    repository = MarketDataRepository(db_session)
    await positions.sync_positions(
        FakePositionsClient(),  # type: ignore[arg-type]
        repository,
        SESSION,
    )
    await db_session.commit()

    client = FakePositionsClient()
    await positions.sync_positions(
        client,  # type: ignore[arg-type]
        repository,
        later,
        sessions=[SESSION, later],
    )

    assert client.discoveries == []
    assert client.calls == [("SBRF_F", later)]


@pytest.mark.db
async def test_sessions_older_than_collected_are_still_requested(
    db_session: AsyncSession,
) -> None:
    """Отметка из данных — нижняя граница, а не ответ.

    Воспроизводит дефект, найденный на стенде: догон позиций за более раннюю
    дату пропускал 69 активов из 69 как «до появления инструмента», хотя
    позиции по ним собраны позже. История не добралась бы никогда.
    """
    earlier = SESSION - dt.timedelta(days=7)
    await _seed_assets(db_session, ["SBER"], sessions=[earlier, SESSION])
    repository = MarketDataRepository(db_session)
    await positions.sync_positions(
        FakePositionsClient(),  # type: ignore[arg-type]
        repository,
        SESSION,
    )
    await db_session.commit()

    client = FakePositionsClient(available={("SBRF_F", earlier): FakeSnapshot()})
    written = await positions.sync_positions(
        client,  # type: ignore[arg-type]
        repository,
        earlier,
        sessions=[earlier, SESSION],
    )

    assert ("SBRF_F", earlier) in client.calls
    assert written.rows_written == 1


@pytest.mark.db
async def test_contract_without_probe_hits_is_still_asked(
    db_session: AsyncSession,
) -> None:
    """Отрицательные пробы отсутствия истории не доказывают.

    Прежде этот тест требовал обратного — «искали по всему окну и не нашли,
    значит обращения бесполезны», — и это было правилом потери данных: одна
    отрицательная проба в законно пустой день закрывала контракт, торгуемый
    годами, а данные между пробами терялись целиком.

    Спрошенная пара без значений даёт `EmptyPositionsError`: ноль значений на
    выполненных обращениях — неуспех, а не успешная пустота (FR-018, FR-032).
    """
    await _seed_assets(db_session, ["SBER"])
    client = FakePositionsClient(available={})

    result = await positions.sync_positions(
        client,  # type: ignore[arg-type]
        MarketDataRepository(db_session),
        SESSION,
        sessions=[SESSION],
    )

    assert client.calls == [("SBRF_F", SESSION)]
    assert result.complete is False


@pytest.mark.db
async def test_empty_rows_are_never_written(db_session: AsyncSession) -> None:
    """Пустая строка заставляла догон считать сессию закрытой навсегда."""
    await _seed_assets(db_session, ["SBER"])
    client = FakePositionsClient(
        available={
            ("SBRF_F", SESSION): FakeSnapshot(
                fiz_long=None, fiz_short=None, jur_long=None, jur_short=None
            )
        }
    )
    repository = MarketDataRepository(db_session)

    result = await positions.sync_positions(client, repository, SESSION)  # type: ignore[arg-type]

    await db_session.rollback()
    assert await repository.positions_for_window([SESSION]) == []
    assert result.complete is False


async def _seed_assets(
    session: AsyncSession,
    tickers: list[str],
    contracts: dict[str, str] | None = None,
    sessions: list[dt.date] | None = None,
) -> None:
    """Бумаги с историей и их связи с контрактами.

    Связь заводится здесь, а не передаётся в источник: соответствие «бумага →
    семейство контрактов» живёт в хранилище, и второго пути к нему нет.

    **Дата начала связи здесь заведомо раньше окна, и это упрощение.** Оно
    стоило проекту регрессии: во всех сценариях состава связь засевалась за год
    до окна, и случай «связь появилась ПОЗЖЕ окна» не проверялся ни разу —
    именно он и сломался. Проверяется он отдельно, в
    `tests/integration/test_catchup_before_links.py`; здесь же проверяются
    правила сбора сессии, которым дата начала связи безразлична.
    """
    repository = MarketDataRepository(session)
    known = {"SBER": "SBRF_F"} if contracts is None else contracts
    days = sessions or [SESSION]
    await repository.add_trading_sessions(days)
    for ticker in tickers:
        # Связь заводится только тем бумагам, у которых контракт есть: её
        # отсутствие и означает «фьючерса нет».
        if ticker in known:
            await repository.open_link(
                asset_id=f"EQ_AST_{ticker}",
                contract_code=known[ticker],
                valid_from=SESSION - dt.timedelta(days=365),
                chosen_by="underlying_and_emitter",
            )
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSION)
        await repository.upsert_price_series(f"EQ_PRS_{ticker}", f"EQ_AST_{ticker}", SESSION)
        # Наблюдение за КАЖДУЮ засеваемую сессию: позиции спрашиваются только у
        # бумаг, торговавшихся в этот день (FR-053), и сессия без котировок
        # означает «кто торговал — неизвестно», а не «торговали все».
        await repository.upsert_daily_bars(
            [
                DailyBar(
                    asset_id=f"EQ_AST_{ticker}",
                    price_series_id=f"EQ_PRS_{ticker}",
                    session_date=day,
                    open=Decimal("1"),
                    high=Decimal("1"),
                    low=Decimal("1"),
                    close=Decimal("1"),
                    volume=Decimal("1"),
                )
                for day in days
            ]
        )
    await session.commit()


@pytest.mark.db
async def test_остановка_прерывает_повторы_обращения() -> None:
    """Доводится до конца отправленный запрос — не серия из четырёх.

    У источника четыре попытки с нарастающей паузой, и одно обращение
    тянется до двух минут; проверка остановки стояла только между
    инструментами, поэтому «идущий источник доводится до конца» означало на
    деле эти две минуты (FR-058j).
    """
    import httpx

    from financial_ai.market_data.interrupt import SourceStoppedError
    from financial_ai.market_data.sources.positions_client import PositionsClient

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="сервис недоступен")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PositionsClient(Settings(), http, should_stop=lambda: True)
        # Остановка — команда человека, и выдавать её за неисправность биржи
        # нельзя: исход отдельный (T206, FR-050).
        with pytest.raises(SourceStoppedError, match="повтор отменён остановкой"):
            await client.fetch("SBRF_F", SESSION)

    # Одна попытка, а не четыре: повторы отменены остановкой.
    assert attempts["n"] == 1


@pytest.mark.db
async def test_без_остановки_повторы_идут_как_прежде() -> None:
    """Обратная форма: отмена повторов не должна отменить сами повторы."""
    import httpx

    from financial_ai.market_data.sources.positions_client import PositionsClient

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="сервис недоступен")

    quick = Settings(
        market_data_positions_retries=3,
        market_data_positions_retry_backoff_seconds=0,
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PositionsClient(quick, http)
        with pytest.raises(PositionsSourceError):
            await client.fetch("SBRF_F", SESSION)

    assert attempts["n"] == 3
    assert client.metrics.to_dict()["attempts"] == 3
    assert client.metrics.to_dict()["retries"] == 2


# --- T206: сбой не читается отсутствием ---------------------------------------


async def test_failed_probe_is_not_read_as_absence() -> None:
    """Сбой обращения при поиске передаётся наружу, а не становится «нет данных»."""

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="сервис недоступен")

    http = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    async with PositionsClient(_settings(), client=http) as client:
        with pytest.raises(PositionsSourceError):
            await client.first_available_date("SBRF_F", [SESSION])

    assert client.searched_for_first_date("SBRF_F") is False


async def test_stop_cancels_retries_with_its_own_outcome() -> None:
    """Остановка прекращает повторы и называется своим исходом, не ошибкой источника."""
    from financial_ai.market_data.interrupt import SourceStoppedError

    attempts = {"n": 0}

    def failing(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="сервис недоступен")

    http = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    client = PositionsClient(_settings(), http, should_stop=lambda: True)
    with pytest.raises(SourceStoppedError):
        await client.fetch("SBRF_F", SESSION)

    assert attempts["n"] == 1
