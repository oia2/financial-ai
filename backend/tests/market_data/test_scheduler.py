"""Тесты планировщика сбора.

Проверяется решение «есть ли что собирать», а не сам сбор: собственно сбор
покрыт в test_ingest_cycle, а определение закрытости сессии — в test_advance.

Решение принимается по **хранилищу**. Прежняя версия помнила его в памяти
процесса, и память давала два дефекта: перезапуск после времени сбора приводил
к повторному сбору той же сессии, а неудачная попытка блокировала повтор до
следующего дня.
"""

from __future__ import annotations

import datetime as dt
import inspect
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import advance, ingest
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.scheduler import MarketDataScheduler
from financial_ai.market_data.sources import trading_calendar

SESSIONS = [dt.date(2026, 8, 27), dt.date(2026, 8, 28)]
ASOF = SESSIONS[-1]
EVENING = dt.datetime(2026, 8, 28, 20, 0)


def _scheduler(after_close: str = "19:30") -> MarketDataScheduler:
    settings = Settings(
        market_data_ingest_after_close=after_close,
        market_data_price_window_sessions=len(SESSIONS),
        market_data_catchup_window_sessions=len(SESSIONS),
    )
    return MarketDataScheduler(settings)


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("300"),
        high=Decimal("301"),
        low=Decimal("299"),
        close=Decimal("300.5"),
        volume=Decimal("1000"),
    )


async def _seed(session: AsyncSession, collected: list[dt.date]) -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])
    await session.commit()


async def test_disabled_scheduler_does_not_start() -> None:
    settings = Settings(market_data_enabled=False)
    scheduler = MarketDataScheduler(settings)
    await scheduler.start()
    await scheduler.stop()
    assert scheduler._task is None


# --- решение по хранилищу (FR-030–FR-032) ------------------------------------


@pytest.mark.db
async def test_collected_session_is_not_collected_again(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перезапуск после времени сбора не приводит к повторному сбору.

    Прежняя версия держала отметку в памяти процесса, и рестарт стирал её
    вместе с фактом, что сессия уже собрана.
    """
    await _seed(db_session, collected=SESSIONS)

    called: list[dt.date | None] = []

    async def spy(session: object, settings: object, day: dt.date | None = None) -> object:
        called.append(day)
        return ingest.IngestResult(run_id="run", session_date=day)

    monkeypatch.setattr(ingest, "ingest_session", spy)
    monkeypatch.setattr(advance, "moscow_now", lambda: EVENING)

    scheduler = _scheduler()
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: EVENING)
    await scheduler._ingest_once()

    assert called == []


@pytest.mark.db
async def test_failed_attempt_does_not_block_a_retry_the_same_day(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Неудачная попытка не закрывает день.

    Прежняя версия ставила отметку независимо от исхода: сбой биржи в 19:30
    означал, что сегодня сбора уже не будет.
    """
    await _seed(db_session, collected=[SESSIONS[0]])

    attempts: list[dt.date | None] = []

    async def failing(session: object, settings: object, day: dt.date | None = None) -> object:
        attempts.append(day)
        return ingest.IngestResult(run_id="run", session_date=day)

    monkeypatch.setattr(ingest, "ingest_session", failing)
    monkeypatch.setattr(advance, "moscow_now", lambda: EVENING)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: EVENING)

    scheduler = _scheduler()
    await scheduler._ingest_once()
    await scheduler._ingest_once()

    # Сессия так и не собралась, поэтому вторая попытка состоялась.
    assert attempts == [ASOF, ASOF]


# --- догон не запускается сам (FR-001, SC-001) -------------------------------


@pytest.mark.db
async def test_daily_run_makes_no_catchup_calls(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ежедневный прогон собирает закрытую сессию и НЕ порождает догона.

    Неуправляемый догон ушёл на 2909 обращений к бирже без спроса и без
    возможности вмешаться. Теперь историю добирает человек, и проверяется это
    по факту вызова, а не по тексту.
    """
    await _seed(db_session, collected=[SESSIONS[0]])

    calls: list[object] = []

    async def spy_catch_up(*args: object, **kwargs: object) -> ingest.CatchupResult:
        calls.append(args)
        return ingest.CatchupResult()

    async def fake_ingest(session: object, settings: object, day: dt.date | None = None) -> object:
        return ingest.IngestResult(run_id="run", session_date=day)

    monkeypatch.setattr(ingest, "catch_up", spy_catch_up)
    monkeypatch.setattr(ingest, "ingest_session", fake_ingest)
    monkeypatch.setattr(advance, "moscow_now", lambda: EVENING)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: EVENING)

    await _scheduler()._ingest_once()

    assert calls == []


def test_daily_pipeline_does_not_call_catchup() -> None:
    """И полный цикл «сбор → набор → ранжирование» тоже не догоняет."""
    source = inspect.getsource(ingest.ingest_and_rank)
    assert "await catch_up(" not in source


# --- граница данных двигается сама (FR-006, FR-028, US2/AC1) ------------------


@pytest.mark.db
async def test_tick_synchronises_the_trading_calendar(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тик синхронизирует календарь, иначе граница данных не двигается.

    Пропущенные сессии вычисляются по **сохранённому** календарю. Пока он не
    обновлён, новых дат в нём нет, планировщик не видит работы и не собирает —
    а значит и календарь не обновляет. Замкнутый круг: после простоя система
    честно, но бесполезно стоит на старой дате. Наблюдалось на живом стенде.
    """
    await _seed(db_session, collected=SESSIONS)

    synced: list[object] = []

    async def spy_calendar(*args: object, **kwargs: object) -> int:
        synced.append(args)
        return 0

    async def fake_ingest(session: object, settings: object, day: dt.date | None = None) -> object:
        return ingest.IngestResult(run_id="run", session_date=day)

    monkeypatch.setattr(trading_calendar, "sync_trading_calendar", spy_calendar)
    monkeypatch.setattr(ingest, "ingest_session", fake_ingest)
    monkeypatch.setattr(advance, "moscow_now", lambda: EVENING)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: EVENING)

    await _scheduler()._ingest_once()

    assert synced, "календарь не синхронизирован: граница данных останется на месте"


@pytest.mark.db
async def test_calendar_is_not_synchronised_on_every_tick(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Один тик в минуту не означает обращения к бирже в минуту.

    Календарь меняется раз в сутки, и спрашивать его чаще — это тысяча
    обращений в день заведомо ни за чем. Признак «сегодня уже спрашивали»
    берётся из хранилища исходов сбора, а не из памяти процесса: перезапуск не
    должен его терять.
    """
    await _seed(db_session, collected=SESSIONS)

    synced: list[object] = []

    async def spy_calendar(*args: object, **kwargs: object) -> int:
        synced.append(args)
        return 0

    async def fake_ingest(session: object, settings: object, day: dt.date | None = None) -> object:
        return ingest.IngestResult(run_id="run", session_date=day)

    monkeypatch.setattr(trading_calendar, "sync_trading_calendar", spy_calendar)
    monkeypatch.setattr(ingest, "ingest_session", fake_ingest)
    monkeypatch.setattr(advance, "moscow_now", lambda: EVENING)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: EVENING)

    scheduler = _scheduler()
    await scheduler._ingest_once()
    await scheduler._ingest_once()
    await scheduler._ingest_once()

    assert len(synced) == 1

    # Отметка лежит в хранилище, а не в памяти: новый планировщик, как после
    # перезапуска процесса, повторно календарь не спрашивает.
    await MarketDataScheduler(_scheduler()._settings)._ingest_once()
    assert len(synced) == 1


# --- разрыв сверх предела не догоняется (FR-009, FR-029, US2/AC3) ------------


@pytest.mark.db
async def test_gap_beyond_the_limit_is_not_collected_even_over_many_ticks(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разрыв сверх предела не собирается ни за один тик, ни за десять.

    Нарезать разрыв по три сессии за тик — то же самое, что догонять его
    автоматически, только медленно: через несколько минут он закрыт целиком, а
    человек об этом не просил. Предел на то и предел.
    """
    days = [dt.date(2026, 8, 20) + dt.timedelta(days=i) for i in range(10)]
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(days)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", days[-1])
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", days[-1])
    await repository.upsert_daily_bars([_bar(days[0])])
    await db_session.commit()

    attempts: list[dt.date | None] = []

    async def spy_ingest(session: object, settings: object, day: dt.date | None = None) -> object:
        attempts.append(day)
        return ingest.IngestResult(run_id="run", session_date=day)

    async def fake_calendar(*args: object, **kwargs: object) -> int:
        return 0

    evening = dt.datetime(2026, 8, 29, 20, 0)
    monkeypatch.setattr(trading_calendar, "sync_trading_calendar", fake_calendar)
    monkeypatch.setattr(ingest, "ingest_session", spy_ingest)
    monkeypatch.setattr(advance, "moscow_now", lambda: evening)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: evening)

    settings = Settings(
        market_data_price_window_sessions=len(days),
        market_data_catchup_window_sessions=len(days),
        market_data_startup_recovery_max_sessions=3,
    )
    scheduler = MarketDataScheduler(settings)
    for _ in range(5):
        await scheduler._ingest_once()

    assert attempts == [], "разрыв сверх предела собран автоматически"
