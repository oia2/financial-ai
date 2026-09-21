"""Тесты источника позиций по фьючерсам.

Образцы разметки сняты с **настоящих ответов** биржи 2026-09-04 (данные за
2026-08-28, контракт `SBRF_F`), а не придуманы. Все четыре дефекта этой фичи
были расхождением с настоящим источником, а тесты на подделках при этом
проходили; подделка, срисованная с живого ответа, ловит хотя бы разбор.

Живая сверка — отдельной командой вне гейта: `cli verify-positions` (FR-025).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import links
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import positions
from financial_ai.market_data.sources import positions_client as module
from financial_ai.market_data.sources.positions_client import (
    PositionFetchKind,
    PositionsClient,
    PositionsSourceError,
    parse_page_state,
    parse_snapshot,
)
from tests.market_data.conftest import FakePositionsClient, FakeSnapshot

SESSION = dt.date(2026, 8, 28)
OTHER = dt.date(2026, 8, 27)

NBSP = " "

# Живой ответ по SBRF_F: строка открытых позиций идёт ПЕРВОЙ.
LIVE_TABLE = f"""
<span class="text-center"><b> Данные на 28 августа 2026 </b></span>
<table class="table1 _full-width table1">
<tr>
<th rowspan="2">&nbsp;</th>
<th colspan="2">Физические лица</th>
<th colspan="2">Юридические лица</th>
<th rowspan="2">Совокупный объем открытых позиций</th>
</tr>
<tr>
<th>Длинные позиции</th><th>Короткие позиции</th>
<th>Длинные позиции</th><th>Короткие позиции</th>
</tr>
<tr><td align="center" colspan="6"><b>Фьючерс</b></td></tr>
<tr><td>Количество договоров (контрактов), шт.</td>
<td align="right">125{NBSP}484</td>
<td align="right">88{NBSP}768</td>
<td align="right">388{NBSP}054</td>
<td align="right">424{NBSP}770</td>
<td align="right">1{NBSP}027{NBSP}076</td>
</tr>
<tr><td>Изменение количества договоров (контрактов) по отношению к предыдущему дню, шт.</td>
<td align="right">459</td><td align="right">-3{NBSP}485</td>
<td align="right">-4{NBSP}574</td><td align="right">-630</td>
<td align="right">-8{NBSP}230</td>
</tr>
<tr><td>Относительное изменение количества договоров (контрактов), в %</td>
<td align="right">0,37</td><td align="right">-3,78</td>
<td align="right">-1,17</td><td align="right">-0,15</td>
<td align="right">-0,79</td>
</tr>
<tr><td>Количество лиц, имеющих открытые позиции</td>
<td align="right">3{NBSP}296</td><td align="right">1{NBSP}646</td>
<td align="right">33</td><td align="right">65</td>
<td align="right">5{NBSP}040</td>
</tr>
</table>
"""

# Ответ того же дня с ДРУГИМ порядком строк: сначала число лиц. Это не гипотеза —
# оба варианта сняты с биржи в один день. Отображение по позиции, как в
# оригинале, записало бы сюда число держателей позиций.
REORDERED_TABLE = f"""
<span class="text-center"><b> Данные на 28 августа 2026 </b></span>
<table class="table1 _full-width table1">
<tr><td>Количество лиц, имеющих открытые позиции</td>
<td align="right">1{NBSP}196</td><td align="right">592</td>
<td align="right">3</td><td align="right">33</td>
<td align="right">1{NBSP}824</td>
</tr>
<tr><td>Количество договоров (контрактов), шт.</td>
<td align="right">182{NBSP}003</td><td align="right">81{NBSP}175</td>
<td align="right">4{NBSP}594</td><td align="right">105{NBSP}422</td>
<td align="right">373{NBSP}194</td>
</tr>
</table>
"""

# Скрытые поля и список инструментов — тоже с живой страницы, значения укорочены.
_QUOT = "&#39;"
_POSTBACK = (
    f"javascript:__doPostBack({_QUOT}ctl00$PageContent$RepeaterInstr$ctl{{n}}$Instrum{_QUOT},"
    f"{_QUOT}{_QUOT})"
)
_ANCHOR_ID = "ctl00_PageContent_RepeaterInstr_ctl{n}_Instrum"

LIVE_STATE = f"""
<input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden"
       value="/65DkHKNQexV34pBJcsjuy2anFSl"/>
<input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden"
       value="20BEEBED"/>
<input id="__EVENTVALIDATION" name="__EVENTVALIDATION" type="hidden"
       value="mwVzjkKDRwloB0OJAehvBu7"/>
<a href="{_POSTBACK.format(n="497")}" id="{_ANCHOR_ID.format(n="497")}">(SBRF_F)
   Фьючерсный контракт на обыкновенные акции ПАО Сбербанк</a>
<a href="{_POSTBACK.format(n="000")}" id="{_ANCHOR_ID.format(n="000")}">(GAZR_F)
   Фьючерсный контракт на обыкновенные акции ПАО "Газпром"</a>
"""

EMPTY_PAGE = "<html><body><p>Данных нет</p></body></html>"


# --- разбор ответа (contracts/positions-source.md) ----------------------------


def test_open_positions_row_is_parsed() -> None:
    """ФИЗ и ЮР по сторонам — из строки «Количество договоров»."""
    snapshot = parse_snapshot(LIVE_TABLE)

    assert snapshot is not None
    assert snapshot.trade_date == SESSION
    assert snapshot.fiz_long == Decimal("125484")
    assert snapshot.fiz_short == Decimal("88768")
    assert snapshot.jur_long == Decimal("388054")
    assert snapshot.jur_short == Decimal("424770")


def test_row_is_found_by_title_not_by_position() -> None:
    """Порядок строк в живых ответах различается — искать по номеру нельзя."""
    snapshot = parse_snapshot(REORDERED_TABLE)

    assert snapshot is not None
    assert snapshot.fiz_long == Decimal("182003")
    assert snapshot.fiz_long != Decimal("1196")


def test_persons_row_is_not_taken_for_positions() -> None:
    """Соседние строки таблицы в наблюдение не попадают."""
    snapshot = parse_snapshot(REORDERED_TABLE)

    assert snapshot is not None
    for value in (snapshot.fiz_long, snapshot.fiz_short, snapshot.jur_long, snapshot.jur_short):
        assert value not in (Decimal("1196"), Decimal("592"), Decimal("1824"))


def test_sides_are_split_into_fiz_and_jur() -> None:
    """Разделение на ФИЗ и ЮР перенесено из оригинала."""
    snapshot = parse_snapshot(LIVE_TABLE)

    assert snapshot is not None
    assert (snapshot.fiz_long, snapshot.fiz_short) != (snapshot.jur_long, snapshot.jur_short)


def test_response_without_table_is_absence_not_crash() -> None:
    """Нет таблицы — нет данных. Это не ошибка разбора."""
    assert parse_snapshot(EMPTY_PAGE) is None


def test_empty_snapshot_is_distinguishable_from_filled() -> None:
    """На этом различии держится FR-018."""
    snapshot = parse_snapshot(LIVE_TABLE)

    assert snapshot is not None
    assert snapshot.has_values is True


def test_page_state_is_read_from_the_response() -> None:
    """Состояние формы берётся у страницы, а не из захваченного файла."""
    state = parse_page_state(LIVE_STATE)

    assert state.viewstate.startswith("/65DkHKNQex")
    assert state.generator == "20BEEBED"
    assert state.validation.startswith("mwVzjkKD")
    assert state.is_ready


def test_instrument_list_is_read_from_the_response() -> None:
    """Список контрактов — оттуда же, вместе с целью постбэка."""
    state = parse_page_state(LIVE_STATE)

    assert set(state.instruments) == {"SBRF_F", "GAZR_F"}
    assert (
        state.instruments["SBRF_F"].event_target == "ctl00$PageContent$RepeaterInstr$ctl497$Instrum"
    )
    assert "Сбербанк" in state.instruments["SBRF_F"].selected_text


# --- соответствие акции и контракта (research.md §11) ------------------------


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


# --- обмен: темп, повторы, даты (FR-024a — FR-024d) --------------------------


class Recorder:
    """Подделка сайта биржи. Считает обращения и отдаёт заготовленное."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.body = LIVE_STATE + LIVE_TABLE

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, text=self.body)

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


async def test_pause_is_kept_between_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Между пачками выдерживается пауза, а размер пачки — из конфигурации."""
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    recorder = Recorder()
    settings = _settings(
        market_data_positions_batch_size=2,
        market_data_positions_batch_pause_seconds=0.25,
    )
    # Даты РАЗНЫЕ: повтор одной и той же пары «контракт — дата» второго
    # обращения больше не делает, и пять одинаковых вызовов проверяли бы уже
    # не темп, а кеш снимков (T206).
    async with PositionsClient(settings, client=recorder.client()) as client:
        for shift in range(5):
            await client.fetch("SBRF_F", SESSION - dt.timedelta(days=shift))

    # Шесть обращений: одно за состоянием формы и пять за данными.
    assert len(recorder.requests) == 6
    assert pauses == [0.25, 0.25]


async def test_batch_size_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Темп — вопрос эксплуатации, а не свойство кода."""
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    recorder = Recorder()
    settings = _settings(
        market_data_positions_batch_size=5,
        market_data_positions_batch_pause_seconds=0.5,
    )
    async with PositionsClient(settings, client=recorder.client()) as client:
        for shift in range(5):
            await client.fetch("SBRF_F", SESSION - dt.timedelta(days=shift))

    assert pauses == [0.5]


async def test_state_is_requested_once_not_per_instrument() -> None:
    """Список инструментов спрашивается один раз, а не на каждый контракт."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        await client.fetch("SBRF_F", SESSION)
        await client.fetch("GAZR_F", SESSION)

    assert len(recorder.requests) == 3


async def test_snapshot_for_another_date_is_not_returned() -> None:
    """Биржа отдаёт последний доступный снимок — записывать его нельзя."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", OTHER)

    assert result.kind is PositionFetchKind.UNKNOWN
    assert result.reason_code == "foreign_trade_date"


async def test_unknown_contract_costs_no_request() -> None:
    """Контракта нет в списке — обращения не выполняется (FR-022)."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        await client.known_contracts(SESSION)
        before = len(recorder.requests)

        result = await client.fetch("НЕТТАКОГО_F", SESSION)
        assert result.kind is PositionFetchKind.UNKNOWN
        assert result.reason_code == "not_in_current_list"
        assert len(recorder.requests) == before


async def test_missing_table_is_unknown_and_cached() -> None:
    recorder = Recorder()
    recorder.body = LIVE_STATE
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        first = await client.fetch("SBRF_F", SESSION)
        spent = len(recorder.requests)
        again = await client.fetch("SBRF_F", SESSION)

    assert first.kind is PositionFetchKind.UNKNOWN
    assert first.reason_code == "positions_table_missing"
    assert again == first
    assert len(recorder.requests) == spent


async def test_exact_date_empty_row_confirms_absence() -> None:
    recorder = Recorder()
    recorder.body = (
        LIVE_STATE
        + """
    <span class="text-center"><b> Данные на 28 августа 2026 </b></span>
    <table class="table1 _full-width table1">
      <tr><td>Количество договоров (контрактов), шт.</td>
      <td></td><td></td><td></td><td></td><td></td></tr>
    </table>
    """
    )
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", SESSION)

    assert result.kind is PositionFetchKind.CONFIRMED_ABSENCE
    assert result.reason_code == "exact_date_empty_position_row"


async def test_zero_positions_are_values() -> None:
    recorder = Recorder()
    recorder.body = LIVE_STATE + LIVE_TABLE.replace(f"125{NBSP}484", "0").replace(
        f"88{NBSP}768", "0"
    ).replace(f"388{NBSP}054", "0").replace(f"424{NBSP}770", "0")
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        result = await client.fetch("SBRF_F", SESSION)

    assert result.kind is PositionFetchKind.VALUE
    assert result.snapshot is not None
    assert result.snapshot.fiz_long == Decimal("0")


async def test_retries_are_bounded_and_reported() -> None:
    """Повторы конечны, а исчерпание — ошибка с причиной."""

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="сервис недоступен")

    client = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    async with PositionsClient(_settings(), client=client) as positions:
        with pytest.raises(PositionsSourceError):
            await positions.fetch("SBRF_F", SESSION)


# --- первая доступная дата (FR-024b) -----------------------------------------


async def test_without_sessions_nothing_is_searched() -> None:
    """Искать не по чему — значит и не искали: незнание не повод не спрашивать."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        found = await client.first_available_date("SBRF_F", [])

        assert found is None
        assert client.searched_for_first_date("SBRF_F") is False
        assert recorder.requests == []


async def test_search_marks_the_contract_as_searched() -> None:
    """Отличать «искали и не нашли» от «ещё не искали» обязан вызывающий."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        await client.first_available_date("SBRF_F", [OTHER, SESSION])

        assert client.searched_for_first_date("SBRF_F") is True


async def test_first_available_date_is_searched_once() -> None:
    """Поиск выполняется однократно, а не при каждом прогоне."""
    recorder = Recorder()
    sessions = [SESSION - dt.timedelta(days=n) for n in range(10, 0, -1)]

    async with PositionsClient(
        _settings(market_data_positions_discover_step=5), client=recorder.client()
    ) as client:
        first = await client.first_available_date("SBRF_F", sessions)
        spent = len(recorder.requests)
        again = await client.first_available_date("SBRF_F", sessions)

    assert first == again
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

    with pytest.raises(positions.EmptyPositionsError, match="ни одного значения"):
        await positions.sync_positions(
            client,  # type: ignore[arg-type]
            MarketDataRepository(db_session),
            SESSION,
        )


@pytest.mark.db
async def test_real_partial_coverage_is_still_a_success(db_session: AsyncSession) -> None:
    """Настоящая частичность — норма: значения есть хотя бы по части бумаг."""
    await _seed_assets(db_session, ["SBER", "GAZP"], {"SBER": "SBRF_F", "GAZP": "GAZR_F"})
    client = FakePositionsClient(available={("SBRF_F", SESSION): FakeSnapshot()})

    written = await positions.sync_positions(
        client,  # type: ignore[arg-type]
        MarketDataRepository(db_session),
        SESSION,
    )

    assert written == 1


@pytest.mark.db
async def test_collected_pair_is_not_asked_again(db_session: AsyncSession) -> None:
    """FR-024c: повторный догон собранного периода не стоит ничего."""
    await _seed_assets(db_session, ["SBER"])
    repository = MarketDataRepository(db_session)
    first = FakePositionsClient()
    await positions.sync_positions(first, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    second = FakePositionsClient()
    written = await positions.sync_positions(
        second,  # type: ignore[arg-type]
        repository,
        SESSION,
    )

    assert first.calls == [("SBRF_F", SESSION)]
    assert second.calls == []
    assert written == 0


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
    assert written == 1


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
    assert written == 1


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

    with pytest.raises(positions.EmptyPositionsError):
        await positions.sync_positions(
            client,  # type: ignore[arg-type]
            MarketDataRepository(db_session),
            SESSION,
            sessions=[SESSION],
        )

    assert client.calls == [("SBRF_F", SESSION)]


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

    with pytest.raises(positions.EmptyPositionsError):
        await positions.sync_positions(client, repository, SESSION)  # type: ignore[arg-type]

    await db_session.rollback()
    assert await repository.positions_for_window([SESSION]) == []


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


# --- T206: сбой, отсутствие и повторное использование снимка -----------------
#
# Три вещи, которые этот источник раньше путал между собой: неудавшееся
# обращение, доказанное отсутствие данных и уже полученный ответ. `_has_data()`
# ловил ошибку обмена и возвращал False, поэтому упавшая проба читалась как
# «данных нет», а все упавшие пробы давали источнику исход «ок» с нулём строк.


async def test_failed_probe_is_not_read_as_absence() -> None:
    """Сбой обращения при поиске передаётся наружу, а не становится «нет данных»."""

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="сервис недоступен")

    sessions = [SESSION - dt.timedelta(days=n) for n in range(10, 0, -1)]
    http = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    async with PositionsClient(_settings(), client=http) as client:
        with pytest.raises(PositionsSourceError):
            await client.first_available_date("SBRF_F", sessions)

    # И отрицательный ответ при этом не запомнен: иначе один сбой сети закрыл бы
    # контракт на весь прогон.
    assert client.searched_for_first_date("SBRF_F") is False


async def test_failed_probe_does_not_cache_absence_for_later_calls() -> None:
    """После сбоя поиск повторяется, а не отвечает из запомненного «нет»."""
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, text="сервис недоступен")
        return httpx.Response(200, text=LIVE_STATE + LIVE_TABLE)

    # Окно кончается SESSION: подделка отдаёт снимок именно за эту дату, и
    # удачная проба в принципе возможна — иначе тест не отличил бы
    # «спросили снова» от «ответили запомненным нет».
    sessions = [SESSION - dt.timedelta(days=n) for n in range(3, -1, -1)]
    http = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    async with PositionsClient(_settings(), client=http) as client:
        with pytest.raises(PositionsSourceError):
            await client.first_available_date("SBRF_F", sessions)
        # Второй заход обязан снова пойти в источник — и, раз источник ожил,
        # найти дату. Запомненное «нет» ответило бы `None`, не спросив.
        spent = calls["n"]
        found = await client.first_available_date("SBRF_F", sessions)

        assert calls["n"] > spent
        assert found is not None


async def test_found_snapshot_is_not_requested_twice() -> None:
    """Снимок, полученный при поиске, повторно не запрашивается (FR-022)."""
    recorder = Recorder()
    async with PositionsClient(_settings(), client=recorder.client()) as client:
        found = await client.first_available_date("SBRF_F", [SESSION])
        assert found == SESSION
        spent = len(recorder.requests)

        # Ровно та пара «контракт — дата», которую только что принёс поиск.
        result = await client.fetch("SBRF_F", SESSION)

        assert result.kind is PositionFetchKind.VALUE
        assert result.snapshot is not None
        assert len(recorder.requests) == spent


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
