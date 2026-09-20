"""Готовность данных за дату (US1, FR-001–FR-005).

Главный инвариант фичи: Daily ML не запускается на неполном обязательном окне.
Пропущенный прогон — норма, пропущенные входные данные — нет.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import readiness
from financial_ai.daily_ml import reconcile as reconcile_module

from .conftest import ASOF, SESSIONS, seed

pytestmark = pytest.mark.db


async def test_full_window_is_ready(db_session: AsyncSession, settings: Settings) -> None:
    await seed(db_session)

    assert (await readiness.evaluate(db_session, settings, ASOF)).ready


async def test_gap_inside_the_window_blocks_the_date(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Данные за саму дату есть, а внутри окна дыра — дата не готова.

    Это ровно случай из спецификации: 08.09 собрано, 02–05.09 пропущены.
    Модель считает по окну, а не по одной дате.
    """
    await seed(db_session, collected=[SESSIONS[0], SESSIONS[1], ASOF])

    result = await readiness.evaluate(db_session, settings, ASOF)

    assert not result.ready
    assert result.missing_groups == ["quotes"]
    assert result.reason is not None


async def test_non_session_date_is_never_ready(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Даты, которой нет в календаре, для системы не существует."""
    import datetime as dt

    await seed(db_session)

    assert not (await readiness.evaluate(db_session, settings, dt.date(2026, 9, 5))).ready


async def test_optional_group_does_not_affect_readiness(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Группа вне обязательного перечня на готовность не влияет.

    Позиции и справочники не засеяны вовсе, но в обязательный перечень они не
    входят — и дата готова.
    """
    await seed(db_session)

    assert settings.daily_ml_required_data_groups == ["quotes"]
    assert (await readiness.evaluate(db_session, settings, ASOF)).ready


async def test_latest_ready_finds_the_freshest_complete_date(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Ищется самая свежая готовая дата, а не первая попавшаяся."""
    await seed(db_session)

    assert await readiness.latest_ready(db_session, settings, ASOF) == ASOF


async def test_incomplete_dataset_declaration_blocks_the_run(settings: Settings) -> None:
    """Окончательное слово — за объявлением полноты набора.

    Дешёвая проверка могла устареть между постановкой в очередь и выполнением,
    а инвариант нарушать нельзя.
    """
    incomplete_required = [{"session_date": "2026-08-27", "sources": ["equity_d1"]}]
    incomplete_optional = [{"session_date": "2026-08-27", "sources": ["futures_positions"]}]

    assert not readiness.dataset_is_complete(incomplete_required, settings)
    assert readiness.dataset_is_complete(incomplete_optional, settings)
    assert readiness.dataset_is_complete([], settings)


async def test_not_ready_date_produces_no_run(db_session: AsyncSession, settings: Settings) -> None:
    """Неполный вход не превращается в задание."""
    await seed(db_session, collected=[SESSIONS[0], ASOF])

    result = await reconcile_module.reconcile(db_session, settings)

    assert result.queued == 0
    assert result.latest_data_ready is None
    assert result.notes


async def test_incomplete_input_names_the_blocking_groups(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Неготовая дата называет обязательные группы, из-за которых она неготова.

    Перечень нужен интерфейсу: состояние обязано объяснять, каких данных не
    хватает (US1/AC5), а не только сообщать, что их ждут.
    """
    await seed(db_session, collected=SESSIONS[:-1])

    result = await readiness.evaluate(db_session, settings, ASOF)

    assert not result.ready
    assert result.missing_groups == ["quotes"]


async def test_unchanged_input_is_answered_without_rebuilding_the_dataset(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если после прогона ничего не собирали, набор не пересобирается.

    Пересборка стоит секунд процессорного времени и держит весь процесс: на
    живом стенде из-за неё состояние раздела отвечало 504, а расчёт плана —
    пятнадцать секунд, и всё это время не отвечало ничего.

    Дешёвый ответ обязан быть не только быстрым, но и верным: он опирается на
    то, что содержимое набора — функция от сохранённых данных, а данные
    попадают в хранилище только через сбор, и каждый сбор записан.
    """
    from financial_ai.market_data.calendar import TradingCalendar
    from financial_ai.market_data.repository import MarketDataRepository
    from financial_ai.ranking import dataset as dataset_module

    await seed(db_session)

    window = await TradingCalendar(MarketDataRepository(db_session)).window(
        ASOF, settings.market_data_price_window_sessions
    )

    async def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("набор пересобран, хотя собирать после прогона было нечего")

    monkeypatch.setattr(dataset_module, "build_dataset", forbidden)

    stale = await readiness.is_stale(
        db_session,
        settings,
        ASOF,
        "sha256:любой",
        since=dt.datetime.now(dt.UTC),
        window=(window[0], window[-1]),
    )

    assert stale is False


async def test_changed_window_reopens_the_expensive_check(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Смена глубины окна меняет набор, ничего не собирая.

    Дешёвая ветка обязана это заметить: иначе система молча считала бы прогон
    актуальным на входе, которого он не видел. Границы окна записаны с прогоном
    и сверяются с теми, что дало бы окно сейчас.
    """
    stale = await readiness.is_stale(
        db_session,
        settings,
        ASOF,
        "sha256:любой",
        since=dt.datetime.now(dt.UTC),
        # Окно прогона не совпадает с нынешним — дешёвый ответ недопустим.
        window=(SESSIONS[0] - dt.timedelta(days=30), ASOF),
        cheap_only=True,
    )

    assert stale is True


async def test_range_source_is_complete_after_verified_range_runs(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Диапазонный источник закрывает окно наблюдениями, а не журналом прогонов.

    Ряды ЦБ, Brent, индекс и глобальные ряды забираются одним запросом за весь
    период, а исход записывается на одну дату — конец периода. Счёт по журналу
    объявлял пустыми все остальные сессии, данные за которые лежат рядом.
    Из-за этого группа «глобальные ряды» не могла стать полной ни при каком
    догоне, и обязательным входом пришлось оставить одни котировки.
    """
    from financial_ai.market_data import completeness, groups
    from financial_ai.market_data.models import GlobalDailySeries
    from financial_ai.market_data.repository import MarketDataRepository

    repository = await seed(db_session)
    global_group = next(g for g in groups.GROUPS if g.group_id.value == "global")

    # Наблюдения есть за каждую сессию окна у КАЖДОГО источника группы, а
    # прогон записан только за последнюю — ровно так выглядит диапазонная
    # выборка. Ряд каждого источника свой: у группы одна таблица на четверых,
    # и чужие наблюдения источник за себя не засчитывает (FR-047).
    for day in SESSIONS:
        for series_id in ("CBR_KEY_RATE", "BRENT_FRONT", "IMOEX", "IDX_WEIGHT_IMOEX_SBER"):
            db_session.add(
                GlobalDailySeries(series_id=series_id, session_date=day, value=Decimal("16.5"))
            )
    moment = dt.datetime.now(dt.UTC)
    for source_id in global_group.source_ids:
        await repository.record_run(
            run_id=f"range-run-{source_id}",
            source_id=source_id,
            status="ok",
            started_at=moment,
            finished_at=moment,
            period_from=SESSIONS[0],
            period_till=SESSIONS[-1],
            rows_written=len(SESSIONS),
        )
    await db_session.commit()

    missing = await completeness.missing_sessions(
        MarketDataRepository(db_session), global_group, list(SESSIONS)
    )

    assert missing == [], "наблюдения есть, а группа числится неполной"


async def test_range_source_does_not_close_the_window_for_its_neighbours(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Обратная форма предыдущего правила, и ровно ею дефект и жил.

    Четыре источника «глобальных рядов» пишут в одну таблицу, а наблюдения
    считались по всей таблице: ставка ЦБ за сессию закрывала её сразу и Brent,
    и индексу, и рядам ISS. Правило «по каждому источнику» при этом формально
    выполнялось — проверять ему было нечего (FR-047).
    """
    from financial_ai.market_data import completeness, groups
    from financial_ai.market_data.models import GlobalDailySeries
    from financial_ai.market_data.repository import MarketDataRepository

    await seed(db_session)
    global_group = next(g for g in groups.GROUPS if g.group_id.value == "global")

    for day in SESSIONS:
        db_session.add(
            GlobalDailySeries(series_id="CBR_KEY_RATE", session_date=day, value=Decimal("16.5"))
        )
    await db_session.commit()

    missing = await completeness.missing_sessions(
        MarketDataRepository(db_session), global_group, list(SESSIONS)
    )

    assert missing == list(SESSIONS), "ряд одного источника закрыл сессию всем остальным"


async def test_empty_exchange_answer_still_closes_the_session(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Успешный прогон без наблюдений сессию всё равно закрывает.

    Биржа ответила, данных за день нет — это законный исход, и наблюдений после
    него не появится никогда. Считать такую сессию несобранной значило бы
    перевыбирать её вечно.
    """
    from financial_ai.market_data import completeness, groups
    from financial_ai.market_data.repository import MarketDataRepository

    repository = await seed(db_session, collected=[])
    quotes = next(g for g in groups.GROUPS if g.group_id.value == "quotes")

    now = dt.datetime.now(dt.UTC)
    for day in SESSIONS:
        await repository.record_run(
            run_id=f"empty-{day.isoformat()}",
            source_id="equity_d1",
            status="ok",
            started_at=now,
            session_date=day,
            rows_written=0,
        )
    await db_session.commit()

    missing = await completeness.missing_sessions(
        MarketDataRepository(db_session), quotes, list(SESSIONS)
    )

    assert missing == []
