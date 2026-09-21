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
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import advance, groups, ingest
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.scheduler import MarketDataScheduler
from financial_ai.market_data.sources import trading_calendar
from tests.market_data.verified import record_verified_run

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


async def _seed(
    session: AsyncSession, collected: list[dt.date], *, settings: Settings | None = None
) -> None:
    """Календарь, актив и собранные сессии — с барами И исходами сбора.

    Исходы засеиваются наравне с барами: собранность определяется именно ими, и
    реальный сбор пишет то и другое. Фикстура, писавшая только бары, изображала
    состояние, которого в системе не бывает.
    """
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)

    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])

    moment = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
    for day in collected:
        for group in groups.required(settings or Settings()):
            for source_id in group.source_ids:
                await record_verified_run(
                    repository,
                    run_id=f"seed-{day}",
                    source_id=source_id,
                    started_at=moment,
                    finished_at=moment,
                    session_date=day,
                )

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

    async def failing(
        session: object, settings: object, day: dt.date | None = None, **_: object
    ) -> object:
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

    async def fake_ingest(
        session: object, settings: object, day: dt.date | None = None, **_: object
    ) -> object:
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

    async def fake_ingest(
        session: object, settings: object, day: dt.date | None = None, **_: object
    ) -> object:
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

    async def fake_ingest(
        session: object, settings: object, day: dt.date | None = None, **_: object
    ) -> object:
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
    """История сверх предела не собирается ни за один тик, ни за десять.

    Нарезать разрыв по три сессии за тик — то же самое, что догонять его
    автоматически, только медленно: через несколько минут он закрыт целиком, а
    человек об этом не просил. Предел на то и предел.

    Последняя закрытая сессия под запрет не подпадает и собирается каждый тик
    (FR-046): иначе система переставала бы собирать свежие данные, и отставание
    только росло. Повторно она не пересобирается — после первого тика она уже
    не в списке недостающих.
    """
    days = [dt.date(2026, 8, 20) + dt.timedelta(days=i) for i in range(10)]
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(days)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", days[-1])
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", days[-1])
    await repository.upsert_daily_bars([_bar(days[0])])
    await db_session.commit()

    attempts: list[dt.date | None] = []

    async def spy_ingest(
        session: object, settings: object, day: dt.date | None = None, **_: object
    ) -> object:
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

    # Только последняя закрытая, и только пока она недобрана: история — нет.
    assert set(attempts) <= {days[-1]}, "история сверх предела собрана автоматически"


async def test_paused_scheduler_collects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остановленный сбор не создаёт работы и на биржу не ходит.

    Останавливается создание НОВОЙ работы: тик просто ничего не делает.
    Возобновление возвращает сбор без перезапуска системы (FR-029d).
    """
    called: list[str] = []

    async def spy_advance(*args: object, **kwargs: object) -> object:
        called.append("advance")
        raise AssertionError("остановленный сбор не должен ходить за данными")

    monkeypatch.setattr(advance, "advance", spy_advance)

    scheduler = MarketDataScheduler(Settings())

    assert scheduler.paused is False
    scheduler.set_paused(True)
    assert scheduler.paused is True

    await scheduler._ingest_once()
    assert called == []

    # Возобновление снимает остановку: работа снова ищется.
    scheduler.set_paused(False)
    assert scheduler.paused is False
    with pytest.raises(AssertionError):
        await scheduler._ingest_once()
    assert called == ["advance"]


async def test_automatic_collection_is_visible_as_a_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Автоматический сбор показывается тем же состоянием, что и сбор по кнопке.

    Для человека это одна и та же работа: система идёт на биржу за сессиями.
    Пока состояния не было, автоматический сбор оставался невидимым, хотя длится
    он ровно столько же, сколько запущенный вручную.
    """
    days = [dt.date(2026, 8, 27), dt.date(2026, 8, 28)]
    seen: list[str] = []

    async def fake_advance(
        _session: object,
        _settings: object,
        _now: object = None,
        *,
        on_plan=None,
        on_session_start=None,
        on_session_done=None,
        on_source=None,
        on_skip=None,
        should_stop=None,
    ) -> object:
        on_plan(days, "прогон-испытания")
        seen.append(scheduler.state.status.value)
        for day in days:
            on_session_start(day)
            on_session_done(day, True)
        return SimpleNamespace(limit_exceeded=False, pending=[], collected=days)

    monkeypatch.setattr(advance, "advance", fake_advance)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: dt.datetime.now())

    scheduler = MarketDataScheduler(Settings())
    # Реконсиляция ранжирования к этому тесту не относится и требует базы.
    monkeypatch.setattr(scheduler, "_reconcile_daily_ml", _async_noop)

    # До работы процесса нет: пустой тик не должен мигать баннером.
    assert scheduler.state.status.value == "idle"

    await scheduler._ingest_once()

    # Во время работы состояние — «идёт», и его видит баннер процессов.
    assert seen == ["running"]
    # После — завершено, с посессионным итогом.
    assert scheduler.state.status.value == "finished"
    assert scheduler.state.closed == days
    # Последняя сессия остаётся названной: по ней показывается лента
    # источников законченного прогона. Обнуление стирало с экрана ответ на
    # вопрос «на чём прогон стоял» (FR-025).
    assert scheduler.state.current == days[-1]


async def _async_noop(*args: object, **kwargs: object) -> None:
    return None


async def test_automatic_collection_can_be_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Идущий автоматический сбор останавливается той же командой, что и ручной.

    Человек останавливает то, что видит на экране, а не тот из двух механизмов,
    о котором знать не обязан. Остановка мягкая: начатая сессия доводится до
    конца, следующая не начинается.
    """
    days = [dt.date(2026, 8, 27), dt.date(2026, 8, 28), dt.date(2026, 8, 31)]
    done: list[dt.date] = []

    async def fake_advance(
        _session: object,
        _settings: object,
        _now: object = None,
        *,
        on_plan=None,
        on_session_start=None,
        on_session_done=None,
        on_source=None,
        on_skip=None,
        should_stop=None,
    ) -> object:
        on_plan(days, "прогон-испытания")
        for day in days:
            if should_stop():
                break
            on_session_start(day)
            on_session_done(day, True)
            done.append(day)
            # Останавливаем после первой же сессии.
            scheduler.request_stop()
        return SimpleNamespace(limit_exceeded=False, pending=[], collected=list(done))

    monkeypatch.setattr(advance, "advance", fake_advance)
    monkeypatch.setattr("financial_ai.market_data.scheduler.moscow_now", lambda: dt.datetime.now())

    scheduler = MarketDataScheduler(Settings())
    monkeypatch.setattr(scheduler, "_reconcile_daily_ml", _async_noop)

    await scheduler._ingest_once()

    assert done == days[:1], "остановка не прервала сбор между сессиями"
    assert scheduler.state.status.value == "stopped"

    # Остановка — про ОДИН прогон, а не про выключение режима: следующий тик
    # снова ищет работу. Для «не начинать вовсе» есть пауза автосбора.
    assert scheduler.paused is False
