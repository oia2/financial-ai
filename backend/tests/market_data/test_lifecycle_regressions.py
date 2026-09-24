"""Регрессии анализа 2026-09-23: «один раз собрать, дальше добавлять».

Каждый тест воспроизводит дефект, найденный на рабочей базе или в коде
(`specs/008-collection-view-instrument-links/analysis-2026-09-23.md`), и
закрепляет исправленное поведение. Сеть не используется.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import (
    advance,
    backfill,
    completeness,
    coverage,
    groups,
    ingest,
    journal,
    plan,
)
from financial_ai.market_data.calendar import MOSCOW
from financial_ai.market_data.interrupt import SourceStoppedError
from financial_ai.market_data.iss.client import IssClient, IssConfig, IssError
from financial_ai.market_data.models import IngestRun, IngestSessionOutcome
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import equity_d1, global_series
from financial_ai.market_data.verification import VerificationResult, WorkEvidence
from tests.market_data.test_catchup import ASOF, SESSIONS, FakeIss
from tests.market_data.verified import record_verified_run

pytestmark = pytest.mark.db

BASE = "https://iss.moex.com/iss"


def _moment() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _calendar(session: AsyncSession) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    await session.commit()
    return repository


# --- A1: доказательство только проверенному содержимому ----------------------


def _board(rows: str) -> bytes:
    return (
        b'{"history": {"columns": ["SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE",'
        b' "VOLUME", "VALUE", "NUMTRADES", "WAPRICE"], "data": ['
        + rows.encode()
        + b']}, "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"],'
        b' "data": [[0, 1, 100]]}}'
    )


@pytest.mark.parametrize(
    "row",
    [
        '[null, "2026-09-01", 1, 1, 1, 1, 1, 1, 1, 1]',
        '["SBER", "2026-09-01", 1, 1, 1, "broken-value", 1, 1, 1, 1]',
    ],
    ids=["secid-null", "close-broken"],
)
@respx.mock
async def test_corrupted_board_gets_no_coverage(db_session: AsyncSession, row: str) -> None:
    """Воспроизведение A1 настоящей цепочкой IssClient → источник → run_source.

    Прежде оба ответа получали `ok` и версию покрытия: строка без бумаги
    пропускалась, нераспознанное закрытие становилось NULL.
    """
    repository = await _calendar(db_session)
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, content=_board(row), headers={"content-type": "json"})
    )
    config = IssConfig(base_url=BASE, retries=1, initial_retry_delay_seconds=0.0)
    async with IssClient(config) as client:
        outcome = await ingest.run_source(
            repository,
            "a1",
            equity_d1.SOURCE_ID,
            ASOF,
            lambda: equity_d1.sync_equity_daily(client, repository, ASOF),
        )

    assert outcome.status == ingest.STATUS_FAILED
    assert outcome.failure_kind == plan.FAILURE_SOURCE
    run = await db_session.scalar(select(IngestRun).where(IngestRun.run_id == "a1"))
    assert run is not None and run.coverage_version is None
    assert await repository.work_evidence_for_sessions(equity_d1.SOURCE_ID, [ASOF]) == []


# --- FR-032f: закрывают доказательства, а не статус прогона -------------------


async def test_later_failure_does_not_reopen_proved_session(db_session: AsyncSession) -> None:
    """Поздняя неудачная попытка за доказанную дату не делает её незакрытой.

    Так Brent оставался «с ошибкой» при уже собранных значениях.
    """
    repository = await _calendar(db_session)
    brent_group = next(g for g in groups.GROUPS if "brent" in g.source_ids)
    await record_verified_run(
        repository,
        run_id="ok",
        source_id="brent",
        session_date=ASOF,
        started_at=_moment(),
        finished_at=_moment(),
    )
    await repository.record_run(
        run_id="late-failure",
        source_id="brent",
        status="failed",
        started_at=_moment() + dt.timedelta(minutes=1),
        finished_at=_moment() + dt.timedelta(minutes=1),
        session_date=ASOF,
        failure_reason="временная ошибка",
        failure_kind=plan.FAILURE_SOURCE,
    )
    await db_session.commit()

    closed = await completeness.closed_sessions(repository, brent_group, "brent", [ASOF])

    assert closed == {ASOF}


# --- A2: источник только в своём окне ----------------------------------------


async def test_positions_are_not_asked_outside_their_window(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Все группы, окно цен 5 сессий, позиций 2: позиции — ровно за 2 даты.

    Прежде общий список дат доходил до позиций целиком: на рабочей базе 232
    даты вне их окна.
    """
    await _calendar(db_session)
    asked: list[dt.date] = []

    async def fake_positions(settings, iss, repository, day, *args, **kwargs):  # type: ignore[no-untyped-def]
        asked.append(day)
        return VerificationResult(
            rows_written=0, evidence=(WorkEvidence(day, "applicable_links", "not_applicable"),)
        )

    monkeypatch.setattr(ingest, "_sync_positions", fake_positions)
    settings = Settings(
        market_data_catchup_window_sessions=5,
        market_data_price_window_sessions=5,
        market_data_global_window_sessions=5,
        market_data_positions_window_sessions=2,
    )

    result = await ingest.catch_up(
        db_session,
        settings,
        ASOF,
        client=FakeIss(),
        cbr_client=cbr_client,
        positions_client=object(),  # type: ignore[arg-type]
        sessions=list(SESSIONS),
        source_ids=groups.source_ids_for(groups.GROUPS) - plan.REFERENCE_SOURCES,
        run_id="window-run",
    )

    assert asked == SESSIONS[-2:]
    saved = (
        await db_session.scalars(
            select(IngestSessionOutcome).where(
                IngestSessionOutcome.run_id == "window-run",
                IngestSessionOutcome.session_date == SESSIONS[0],
            )
        )
    ).one()
    assert "futures_positions" not in saved.selected_sources
    assert result.requested == SESSIONS


# --- A4: диапазон продолжается по остатку ------------------------------------


class _History:
    """Глобальные ряды: у RVI нет второй даты."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...], **_: object
    ) -> list[dict[str, object]]:
        self.calls.append((secid, date_from, date_till))
        days = [d for d in SESSIONS[:2] if date_from <= d.isoformat() <= date_till]
        if secid == "RVI":
            days = [d for d in days if d != SESSIONS[1]]
        return [{"SECID": secid, "TRADEDATE": d.isoformat(), "CLOSE": "1.5"} for d in days]


async def test_range_resumes_only_unproved_remainder(db_session: AsyncSession) -> None:
    """Две даты, пять рядов, у RVI нет второй даты: повтор — один запрос RVI.

    Прежде повтор делал все пять запросов за весь период.
    """
    repository = await _calendar(db_session)
    client = _History()
    required = (SESSIONS[0], SESSIONS[1])

    first = await global_series.sync_iss_series_range(
        client,  # type: ignore[arg-type]
        repository,
        SESSIONS[0],
        SESSIONS[1],
        required_dates=required,
    )
    for item in first.evidence:
        await repository.record_work_evidence(
            source_id=global_series.SOURCE_ID,
            session_date=item.session_date,
            work_key=item.work_key,
            result_kind=item.result_kind,
            reason_code=item.reason_code,
            origin_run_id="first",
        )
    await db_session.commit()
    client.calls.clear()

    second = await global_series.sync_iss_series_range(
        client,  # type: ignore[arg-type]
        repository,
        SESSIONS[0],
        SESSIONS[1],
        required_dates=required,
    )

    # Повтор — один запрос RVI за его недоказанную дату. Пустой ответ
    # подтверждённым отсутствием не становится: RVI существует в каждую
    # сессию, и работа остаётся ожиданием публикации (FR-032i).
    assert not first.complete and not second.complete
    assert second.failure_kind == plan.FAILURE_UNPUBLISHED
    assert client.calls == [("RVI", SESSIONS[1].isoformat(), SESSIONS[1].isoformat())]
    group = next(g for g in groups.GROUPS if global_series.SOURCE_ID in g.source_ids)
    closed = await completeness.closed_sessions(
        repository, group, global_series.SOURCE_ID, list(required)
    )
    # Доказанная дата закрыта, хотя весь диапазон неуспешен (FR-032f).
    assert closed == {SESSIONS[0]}


# --- A5: итог ежедневного сбора сохраняется ----------------------------------


async def test_daily_session_outcome_survives_restart(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Журнал ежедневного прогона показывает точный итог, а не «собрано 0»."""
    await _calendar(db_session)

    async def fake_positions(settings, iss, repository, day, *args, **kwargs):  # type: ignore[no-untyped-def]
        return VerificationResult(
            rows_written=0, evidence=(WorkEvidence(day, "applicable_links", "not_applicable"),)
        )

    monkeypatch.setattr(ingest, "_sync_positions", fake_positions)
    settings = Settings(market_data_catchup_window_sessions=5)

    result = await ingest.ingest_session(
        db_session,
        settings,
        ASOF,
        client=FakeIss(),
        cbr_client=cbr_client,
        positions_client=object(),  # type: ignore[arg-type]
        run_id="daily-run",
    )

    saved = (
        await db_session.scalars(
            select(IngestSessionOutcome).where(IngestSessionOutcome.run_id == "daily-run")
        )
    ).one()
    assert saved.outcome == result.session_outcome
    runs = await journal.recent_runs(db_session)
    daily = next(run for run in runs if run.run_id == "daily-run")
    assert daily.history_limited is False
    assert daily.requested == 1


# --- A6: причина незавершённости ----------------------------------------------


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (SourceStoppedError(0, "остановлено"), plan.FAILURE_STOPPED),
        (IssError("MOEX ISS недоступен"), plan.FAILURE_SOURCE),
        (ValueError("ошибка в нашем коде"), plan.FAILURE_INTERNAL),
    ],
)
async def test_failure_kind_is_stored(
    db_session: AsyncSession, error: BaseException, kind: str
) -> None:
    repository = await _calendar(db_session)

    async def action() -> None:
        raise error

    outcome = await ingest.run_source(repository, f"kind-{kind}", "brent", ASOF, action)
    await db_session.commit()

    assert outcome.failure_kind == kind
    run = await db_session.scalar(select(IngestRun).where(IngestRun.run_id == f"kind-{kind}"))
    assert run is not None and run.failure_kind == kind


@pytest.mark.parametrize(
    ("status", "kind", "state"),
    [
        ("stopped", plan.FAILURE_STOPPED, coverage.STATE_INTERRUPTED),
        ("failed", plan.FAILURE_INTERRUPTED, coverage.STATE_INTERRUPTED),
        ("failed", plan.FAILURE_SOURCE, coverage.STATE_SOURCE_ERROR),
        ("failed", plan.FAILURE_INTERNAL, coverage.STATE_INTERNAL_ERROR),
        ("running", None, coverage.STATE_RUNNING),
    ],
)
async def test_group_state_names_the_real_reason(
    db_session: AsyncSession, status: str, kind: str | None, state: str
) -> None:
    """Остановка, перезапуск и идущий сбор — не «ошибка источника» (FR-024e)."""
    repository = await _calendar(db_session)
    await repository.record_run(
        run_id="attempt",
        source_id="equity_d1",
        status=status,
        started_at=_moment(),
        finished_at=None if status == "running" else _moment(),
        session_date=ASOF,
        failure_reason=None if status == "running" else "причина",
        failure_kind=kind,
    )
    await db_session.commit()

    report = await coverage.build_report(
        db_session, Settings(market_data_price_window_sessions=5), ASOF
    )

    quotes = next(group for group in report["groups"] if group["group"] == "quotes")
    assert quotes["state"] == state


# --- A7: справочники в ручном сборе -------------------------------------------


async def test_selected_reference_runs_without_dates(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _calendar(db_session)
    called: list[frozenset[str]] = []

    async def fake_refresh(session, settings, source_ids, **kwargs):  # type: ignore[no-untyped-def]
        called.append(source_ids)
        return []

    monkeypatch.setattr(ingest, "refresh_references", fake_refresh)

    result = await ingest.catch_up(
        db_session,
        Settings(),
        ASOF,
        sessions=[],
        source_ids=frozenset({"equity_sectors"}),
    )

    assert called == [frozenset({"equity_sectors"})]
    assert result.requested == []


def test_reference_scope_is_daily() -> None:
    """Прежде поиск только по ручному плану давал справочнику `session`."""
    assert plan.scope_of("equity_sectors") == plan.DAILY
    assert plan.scope_of("global_series") == plan.PERIOD


# --- A3: первичная загрузка ---------------------------------------------------


class _HistoryClient:
    def __init__(self, end: dt.date) -> None:
        self.end = end
        self.asked: list[str] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...], **_: object
    ) -> list[dict[str, object]]:
        self.asked.append(secid)
        future = self.end + dt.timedelta(days=1)
        return [
            {"SECID": secid, "TRADEDATE": day.isoformat(), "CLOSE": "10"}
            for day in (SESSIONS[0], self.end, future)
        ]


async def test_backfill_loads_known_asset_and_resumes_by_evidence(
    db_session: AsyncSession,
) -> None:
    """Бумага из справочника активов — ещё не загруженная история.

    Прежде после одного обычного сбора загрузка пропускала SBER целиком.
    """
    repository = await _calendar(db_session)
    client = _HistoryClient(ASOF)
    settings = Settings(market_data_backfill_from=SESSIONS[0].isoformat())

    await backfill.backfill_equity(db_session, settings, client, ["SBER"], till=ASOF)  # type: ignore[arg-type]
    await backfill.backfill_equity(db_session, settings, client, ["SBER"], till=ASOF)  # type: ignore[arg-type]

    assert client.asked == ["SBER"]
    stored = await repository.daily_bars_for_window([SESSIONS[0], ASOF, ASOF + dt.timedelta(1)])
    assert sorted(bar.session_date for bar in stored) == [SESSIONS[0], ASOF]
    assert stored[0].close == Decimal("10")


# --- FR-040: календарь после порога -------------------------------------------


@pytest.mark.parametrize(
    ("now", "has_today", "due"),
    [
        (dt.datetime(2026, 9, 22, 19, 40, tzinfo=MOSCOW), False, False),
        (dt.datetime(2026, 9, 22, 19, 46, tzinfo=MOSCOW), False, True),
        (dt.datetime(2026, 9, 22, 23, 30, tzinfo=MOSCOW), True, False),
        (dt.datetime(2026, 9, 26, 20, 30, tzinfo=MOSCOW), False, False),
    ],
    ids=["within-delay", "after-delay", "today-published", "saturday"],
)
async def test_calendar_is_asked_again_until_today_appears(
    db_session: AsyncSession, now: dt.datetime, has_today: bool, due: bool
) -> None:
    """22.09 календарь спросили в 19:30 один раз, и сессия вечером не собралась."""
    repository = MarketDataRepository(db_session)
    # В субботу подтверждена пятница: закрытых будних дней без подтверждения
    # нет, и переспрашивать нечего (FR-040b).
    last_known = dt.date(2026, 9, 25) if now.weekday() >= 5 else dt.date(2026, 9, 21)
    days = [last_known] + ([now.date()] if has_today else [])
    await repository.add_trading_sessions(days)
    asked = now.replace(hour=19, minute=30, second=50)
    await repository.record_run(
        run_id="calendar",
        source_id="trading_calendar",
        status="ok",
        started_at=asked,
        finished_at=asked,
    )
    await db_session.commit()

    settings = Settings(market_data_ingest_after_close="19:30", market_data_retry_after_minutes=15)

    assert await advance.calendar_is_due(repository, now, settings) is due


async def test_backfill_upper_bound_is_last_closed_session(db_session: AsyncSession) -> None:
    """Без явной границы загрузка идёт до последней закрытой сессии календаря."""
    repository = await _calendar(db_session)

    assert await backfill.last_closed_session(repository, Settings()) == ASOF


def test_session_rail_counts_only_sources_in_their_window() -> None:
    """«0 из 5» при датах вне окна позиций: 5 из 5 там быть не могло.

    Позиции вне своего окна исключаются из ленты сессии и возвращаются в
    неё, когда сбор доходит до их окна.
    """
    from financial_ai.market_data.runner import CatchupState

    state = CatchupState(mode=plan.MODE_MANUAL)
    state.begin_session(SESSIONS[0])
    state.note_source("futures_positions", ingest.STATUS_OMITTED)
    rail = {row["source_id"] for row in state.snapshot()["current"]["sources"]}  # type: ignore[index]
    assert "futures_positions" not in rail

    state.begin_session(SESSIONS[1])
    rail = {row["source_id"] for row in state.snapshot()["current"]["sources"]}  # type: ignore[index]
    assert "futures_positions" in rail


async def test_reference_checked_today_is_not_asked_again(db_session: AsyncSession) -> None:
    """Выбор справочников в форме не повод спрашивать их второй раз за сутки.

    Сбор начинался с секторов и лотов, хотя сегодня они уже были проверены.
    """
    repository = await _calendar(db_session)
    for source_id in plan.REFERENCE_SOURCES:
        await repository.record_run(
            run_id="today",
            source_id=source_id,
            status="ok",
            started_at=_moment(),
            finished_at=_moment(),
            session_date=ASOF,
            coverage_version=2,
            coverage_reason="verified_work_evidence",
        )
    await db_session.commit()
    shown: list[tuple[str, str]] = []

    outcomes = await ingest.refresh_references(
        db_session,
        Settings(),
        frozenset(plan.REFERENCE_SOURCES),
        run_id="manual",
        client=object(),  # type: ignore[arg-type]
        on_source=lambda source_id, status, _: shown.append((source_id, status)),
    )

    assert outcomes == []
    assert sorted(shown) == sorted((s, ingest.STATUS_OMITTED) for s in plan.REFERENCE_SOURCES)
