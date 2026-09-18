"""Доведение данных до последней закрытой сессии (US2, FR-006–FR-010).

Догон закрывает дыры внутри окна, но границу окна не двигает: новых сессий он
не видит, пока календарь не расширен. Эта проверка — про границу.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import advance, groups
from financial_ai.market_data.calendar import MOSCOW
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 9, 7),
    dt.date(2026, 9, 8),
    dt.date(2026, 9, 9),
    dt.date(2026, 9, 10),
    dt.date(2026, 9, 11),
]

# Вечер пятницы 11.09 после времени сбора: все пять сессий закрыты.
FRIDAY_EVENING = dt.datetime(2026, 9, 11, 20, 0)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_catchup_window_sessions=len(SESSIONS),
        market_data_startup_recovery_max_sessions=3,
    )


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
    session: AsyncSession,
    collected: list[dt.date],
    *,
    settings: Settings | None = None,
    incomplete: dict[dt.date, str] | None = None,
) -> MarketDataRepository:
    """Календарь, актив и собранные сессии — с барами И исходами сбора.

    Исходы засеиваются наравне с барами, потому что собранность определяется
    именно ими: реальный сбор пишет и то, и другое. Фикстура, писавшая только
    бары, изображала состояние, которого в системе не бывает.

    `incomplete` оставляет источник незакрытым за указанную дату — так
    выглядит день, у которого котировки прошли, а что-то ещё упало.
    """
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSIONS[-1])
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSIONS[-1])

    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])

    required = groups.required(settings or Settings())
    broken = incomplete or {}
    moment = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    for day in collected:
        for group in required:
            for source_id in group.source_ids:
                status = "failed" if broken.get(day) == source_id else "ok"
                await repository.record_run(
                    run_id=f"seed-{day}",
                    source_id=source_id,
                    status=status,
                    started_at=moment,
                    finished_at=moment,
                    session_date=day,
                )

    await session.commit()
    return repository


# --- закрытость сессии (FR-007) ----------------------------------------------


def test_past_session_is_closed(settings: Settings) -> None:
    assert advance.session_is_closed(dt.date(2026, 9, 10), settings, FRIDAY_EVENING)


def test_today_before_close_is_not_closed(settings: Settings) -> None:
    """Незавершённая сессия не собирается: это утечка будущего в признаки."""
    morning = dt.datetime(2026, 9, 11, 12, 0)

    assert not advance.session_is_closed(dt.date(2026, 9, 11), settings, morning)


def test_today_after_close_is_closed(settings: Settings) -> None:
    assert advance.session_is_closed(dt.date(2026, 9, 11), settings, FRIDAY_EVENING)


def test_future_session_is_never_closed(settings: Settings) -> None:
    assert not advance.session_is_closed(dt.date(2026, 9, 14), settings, FRIDAY_EVENING)


def test_custom_close_time_is_respected() -> None:
    """Время сбора — настройка, и его сдвиг сдвигает границу закрытости."""
    late = Settings(market_data_ingest_after_close="21:00")
    today = dt.date(2026, 9, 11)

    assert not advance.session_is_closed(today, late, dt.datetime(2026, 9, 11, 20, 0))
    assert advance.session_is_closed(today, late, dt.datetime(2026, 9, 11, 21, 0))


def test_malformed_close_time_falls_back_without_crashing() -> None:
    """Опечатка в настройке не должна останавливать сбор навсегда."""
    broken = Settings(market_data_ingest_after_close="не время")
    today = dt.date(2026, 9, 11)

    assert advance.session_is_closed(today, broken, dt.datetime(2026, 9, 11, 19, 30))
    assert not advance.session_is_closed(today, broken, dt.datetime(2026, 9, 11, 12, 0))


# --- отставание (FR-006, FR-008, FR-009) --------------------------------------


async def test_pending_sessions_are_those_after_the_last_collected(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session, collected=SESSIONS[:2])

    pending, last_closed = await advance.pending_sessions(db_session, settings, FRIDAY_EVENING)

    assert pending == SESSIONS[2:]
    assert last_closed == SESSIONS[-1]


async def test_nothing_pending_when_everything_collected(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session, collected=SESSIONS)

    pending, last_closed = await advance.pending_sessions(db_session, settings, FRIDAY_EVENING)

    assert pending == []
    assert last_closed == SESSIONS[-1]


async def test_unclosed_session_is_not_pending(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Сегодняшняя сессия до закрытия в работу не берётся."""
    await _seed(db_session, collected=SESSIONS[:-1])
    morning = dt.datetime(2026, 9, 11, 12, 0)

    pending, last_closed = await advance.pending_sessions(db_session, settings, morning)

    assert pending == []
    assert last_closed == SESSIONS[-2]


async def test_large_gap_is_not_collected_when_limit_is_set(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заданный числом предел останавливает сбор ИСТОРИИ.

    Не «собрать три из четырёх»: нарезка истории — тот же неуправляемый догон,
    только растянутый во времени. Предел перестал быть умолчанием, но рычаг
    остался: неуправляемый догон однажды ушёл на 2909 обращений к бирже без
    спроса.

    Последняя закрытая сессия под этот запрет не подпадает (FR-046): прежде
    превышение предела останавливало сбор целиком, система переставала
    собирать и свежие данные, и отставание только росло.
    """
    await _seed(db_session, collected=SESSIONS[:1])

    called: list[dt.date] = []

    async def only_latest(_session: object, _settings: object, day: dt.date, **_: object) -> object:
        called.append(day)
        return SimpleNamespace(succeeded=True, unfinished_sources=[])

    monkeypatch.setattr(advance.ingest, "ingest_session", only_latest)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, settings, FRIDAY_EVENING)

    assert result.limit_exceeded
    assert result.gap_sessions == 3
    assert called == [SESSIONS[-1]]


async def test_default_collects_the_whole_gap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без заданного предела разрыв догоняется целиком, в границах окна догона.

    Ровно тот же разрыв в четыре сессии, что останавливается заданным пределом
    в тесте выше. Умолчание `0` означает «без предела»: граница и так задана
    окном догона, глубже него сессия до модели не доходит.
    """
    unlimited = Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_catchup_window_sessions=len(SESSIONS),
        market_data_startup_recovery_max_sessions=0,
    )
    await _seed(db_session, collected=SESSIONS[:1])

    collected: list[dt.date] = []

    async def collect(_session: object, _settings: object, day: dt.date, **_: object) -> object:
        collected.append(day)
        return SimpleNamespace(succeeded=True, unfinished_sources=[])

    monkeypatch.setattr(advance.ingest, "ingest_session", collect)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, unlimited, FRIDAY_EVENING)

    assert not result.limit_exceeded
    # Последняя закрытая идёт первой, история — за ней (FR-045).
    assert collected[0] == SESSIONS[-1]
    assert sorted(collected) == SESSIONS[1:]
    assert sorted(result.collected) == SESSIONS[1:]
    assert result.pending == []
    assert result.gap_sessions == 0


async def test_hole_inside_the_window_is_collected(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пропуск ВНУТРИ окна догоняется, а не только отставание границы.

    Прежде список считался как «дни после последнего собранного», и дыра
    посреди окна не попадала в него никогда: граница стояла на более поздней
    сессии. Дыра не закрывалась, а ранжирование из-за неё не запускалось —
    полнота требуется по всему окну (FR-029a).
    """
    # Собрано всё, кроме одной сессии посередине. Граница при этом на последней.
    hole = SESSIONS[2]
    await _seed(db_session, collected=[day for day in SESSIONS if day != hole])

    collected: list[dt.date] = []

    async def collect(_session: object, _settings: object, day: dt.date, **_: object) -> object:
        collected.append(day)
        return SimpleNamespace(succeeded=True, unfinished_sources=[])

    monkeypatch.setattr(advance.ingest, "ingest_session", collect)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, settings, FRIDAY_EVENING)

    assert collected == [hole]
    assert result.collected == [hole]
    assert not result.limit_exceeded


def test_retry_delay_holds_back_a_recent_attempt(settings: Settings) -> None:
    """Сессию, которую только что пытались собрать, повторять рано.

    Без выдержки тик раз в минуту превращает устойчивую ошибку в шестьдесят
    обращений в час по одному неотвечающему адресу (FR-029c).
    """
    day = SESSIONS[0]
    waited = settings.model_copy(update={"market_data_retry_after_minutes": 15})
    now = dt.datetime(2026, 9, 11, 20, 0, tzinfo=MOSCOW)

    recent = {day: now - dt.timedelta(minutes=5)}
    assert advance._after_retry_delay([day], recent, waited, now) == []

    old = {day: now - dt.timedelta(minutes=20)}
    assert advance._after_retry_delay([day], old, waited, now) == [day]

    # Ни одной попытки ещё не было — ждать нечего.
    assert advance._after_retry_delay([day], {}, waited, now) == [day]

    # Ноль отключает выдержку целиком.
    off = settings.model_copy(update={"market_data_retry_after_minutes": 0})
    assert advance._after_retry_delay([day], recent, off, now) == [day]


async def test_session_with_a_failed_source_is_collected_again(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """День с упавшим источником не считается собранным.

    Котировки записались, позиции упали — раньше день навсегда числился
    собранным, потому что признаком были бары. Дыра не закрывалась никогда, а
    ранжирование из-за неё не запускалось: полнота требуется по всему окну.
    """
    broken = SESSIONS[2]
    await _seed(
        db_session,
        collected=SESSIONS,
        settings=settings,
        incomplete={broken: "futures_positions"},
    )

    collected: list[dt.date] = []

    async def collect(_session: object, _settings: object, day: dt.date, **_: object) -> object:
        collected.append(day)
        return SimpleNamespace(succeeded=True, unfinished_sources=[])

    monkeypatch.setattr(advance.ingest, "ingest_session", collect)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, settings, FRIDAY_EVENING)

    assert collected == [broken], "день с незакрытым источником не перевыбран"
    assert result.collected == [broken]


async def test_session_stops_being_retried_after_the_attempt_limit(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вечно падающий источник не дёргает биржу бесконечно.

    Источник, недоступный за конкретную дату по своей природе, оставлял бы день
    неполным навсегда — а значит и в списке к сбору навсегда. Предел передаёт
    такой день человеку и управляемому догону, который предела не знает.
    """
    broken = SESSIONS[2]
    capped = settings.model_copy(update={"market_data_session_max_attempts": 1})
    await _seed(
        db_session,
        collected=SESSIONS,
        settings=settings,
        incomplete={broken: "futures_positions"},
    )

    called: list[dt.date] = []

    async def never(*args: object, **kwargs: object) -> object:  # pragma: no cover
        called.append(dt.date.today())
        raise AssertionError("сбор не должен выполняться сверх предела попыток")

    monkeypatch.setattr(advance.ingest, "ingest_session", never)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    result = await advance.advance(db_session, capped, FRIDAY_EVENING)

    assert called == []
    assert result.collected == []


async def _noop(*args: object, **kwargs: object) -> int:
    return 0


class _FakeClient:
    """Клиент биржи, который никуда не ходит."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


async def test_последняя_сессия_собирается_первой(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Свежие данные не ждут разбора истории (FR-045).

    Порядок «от старых к новым» верен для ручного догона, но для ежедневного
    цикла означает, что при отставании сегодняшние данные приходят последними.
    На стенде 2026-09-18 собранное кончалось 11.09 при календаре до 17.09.
    """
    await _seed(db_session, collected=SESSIONS[:1])

    visited: list[dt.date] = []

    async def collect(session: object, cfg: object, day: dt.date, **kwargs: object) -> object:
        visited.append(day)
        return advance.ingest.IngestResult(run_id="r", session_date=day)

    monkeypatch.setattr(advance.ingest, "ingest_session", collect)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    unlimited = settings.model_copy(
        update={"market_data_startup_recovery_max_sessions": len(SESSIONS)}
    )
    await advance.advance(db_session, unlimited, FRIDAY_EVENING)

    assert visited, "сбор не увидел работы"
    assert visited[0] == SESSIONS[-1]


async def test_разрыв_сверх_предела_не_отменяет_сегодня(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пропускается история, а не текущая сессия (FR-046).

    Прежде превышение предела останавливало автоматический сбор целиком:
    система переставала собирать и свежие данные, и отставание только росло.
    """
    await _seed(db_session, collected=SESSIONS[:1])

    visited: list[dt.date] = []
    skipped: list[dt.date] = []

    async def collect(session: object, cfg: object, day: dt.date, **kwargs: object) -> object:
        visited.append(day)
        return advance.ingest.IngestResult(run_id="r", session_date=day)

    monkeypatch.setattr(advance.ingest, "ingest_session", collect)
    monkeypatch.setattr(advance.trading_calendar, "sync_trading_calendar", _noop)
    monkeypatch.setattr(advance, "IssClient", _FakeClient, raising=False)

    await advance.advance(
        db_session,
        settings,
        FRIDAY_EVENING,
        on_skip=lambda day, reason, detail: skipped.append(day),
    )

    assert visited == [SESSIONS[-1]]
    assert SESSIONS[-1] not in skipped
    assert skipped, "история должна быть пропущена с причиной"
