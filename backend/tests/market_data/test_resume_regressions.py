"""Остановка, продолжение и расписание: проверка последствий в настоящей БД."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import advance, completeness, coverage, groups, ingest, journal, plan
from financial_ai.market_data.iss.client import IssClient, IssConfig
from financial_ai.market_data.models import IngestRun
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import cbr, equity_agg, equity_d1, global_series, positions
from financial_ai.market_data.verification import (
    VerificationResult,
    WorkEvidence,
    required_work_keys,
)
from tests.market_data.test_cbr import KEY_RATE_HTML

pytestmark = pytest.mark.db
WINDOW = [dt.date(2026, 8, day) for day in (26, 27, 28)]
DAY = WINDOW[-1]
GLOBAL = next(group for group in groups.GROUPS if cbr.SOURCE_ID in group.source_ids)


async def test_saved_session_outcome_survives_a_new_connection(
    db_session: AsyncSession,
) -> None:
    repository = MarketDataRepository(db_session)
    now = dt.datetime.now(dt.UTC)
    run_id = "persisted-session-outcome"
    selected = frozenset({equity_d1.SOURCE_ID, positions.SOURCE_ID})
    statuses = {equity_d1.SOURCE_ID: "ok", positions.SOURCE_ID: "failed"}
    folded = plan.fold_session_outcome(selected, statuses)

    for source_id, status in statuses.items():
        await repository.record_run(
            run_id,
            source_id,
            status,
            now,
            finished_at=now,
            session_date=DAY,
            trigger="catchup",
        )
    await repository.record_session_outcome(
        run_id=run_id,
        session_date=DAY,
        outcome=folded.outcome,
        selected_sources=selected,
    )
    await db_session.commit()

    async with AsyncSession(bind=db_session.bind) as restarted:
        summary = (await journal.recent_runs(restarted, limit=1))[0]

    assert summary.requested == 1
    assert summary.collected == 0
    assert summary.partial == 1
    assert summary.failed == 0
    assert summary.history_limited is False


async def record(
    repository: MarketDataRepository,
    source: str,
    status: str = "ok",
    day: dt.date = DAY,
    period: tuple[dt.date, dt.date] | None = None,
) -> None:
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id=str(uuid.uuid4()),
        source_id=source,
        status=status,
        session_date=day,
        started_at=moment,
        finished_at=moment,
        period_from=period[0] if period else None,
        period_till=period[1] if period else None,
        coverage_version=2 if status == "ok" else None,
        coverage_reason="test_verified_work" if status == "ok" else None,
    )
    if status == "ok":
        days = [value for value in WINDOW if period and period[0] <= value <= period[1]] or [day]
        for value in days:
            for work_key in required_work_keys(source):
                await repository.record_work_evidence(
                    source_id=source,
                    session_date=value,
                    work_key=work_key,
                    result_kind="value",
                    reason_code="test_verified_work",
                    origin_run_id=None,
                )


def verified(source: str, days: list[dt.date], rows: int = 0) -> VerificationResult:
    return VerificationResult(
        rows_written=rows,
        evidence=tuple(
            WorkEvidence(day, work_key) for day in days for work_key in required_work_keys(source)
        ),
    )


@pytest.mark.parametrize("select_collected", [False, True])
async def test_stop_does_not_reopen_collected_or_unselected_source(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, select_collected: bool
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([DAY])
    await record(repository, equity_agg.SOURCE_ID)
    await repository.commit()
    stop = False

    def on_source(source: str, status: str, outcome: ingest.SourceOutcome | None) -> None:
        nonlocal stop
        if source == equity_d1.SOURCE_ID and status == "ok":
            stop = True

    monkeypatch.setattr(
        equity_d1,
        "sync_equity_daily",
        AsyncMock(return_value=verified(equity_d1.SOURCE_ID, [DAY], 1)),
    )
    selected = {equity_d1.SOURCE_ID}
    if select_collected:
        selected.add(equity_agg.SOURCE_ID)
    outcomes = await ingest._catch_up_session(
        repository,
        "stop-selected",
        Mock(),
        DAY,
        frozenset(selected),
        settings=Settings(),
        positions_client=Mock(),
        sessions=[DAY],
        health=ingest._SourceHealth(3),
        on_source=on_source,
        should_stop=lambda: stop,
    )
    assert all(outcome.status != "stopped" for outcome in outcomes)
    assert equity_agg.SOURCE_ID in await completeness.closed_sources_for(repository, DAY)
    runs = list(
        await db_session.scalars(select(IngestRun).where(IngestRun.run_id == "stop-selected"))
    )
    assert [run.source_id for run in runs] == [equity_d1.SOURCE_ID]


async def test_resume_keeps_successful_range_including_empty_days(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(WINDOW)
    await repository.commit()
    iss_action = AsyncMock(return_value=verified(global_series.SOURCE_ID, WINDOW))
    cbr_action = AsyncMock(return_value=verified(cbr.SOURCE_ID, WINDOW))
    monkeypatch.setattr(global_series, "sync_iss_series_range", iss_action)
    monkeypatch.setattr(ingest, "_sync_cbr_range", cbr_action)

    for start in WINDOW[:2]:
        await ingest._catch_up_ranges(repository, "resume-range", Mock(), start, DAY, None)

    iss_action.assert_awaited_once()
    cbr_action.assert_awaited_once()
    for source in (global_series.SOURCE_ID, cbr.SOURCE_ID):
        assert await completeness.closed_sessions(repository, GLOBAL, source, WINDOW) == set(WINDOW)
    runs = list(await db_session.scalars(select(IngestRun)))
    assert all(run.period_from == WINDOW[0] for run in runs)


async def test_iss_range_does_not_swallow_stop_as_empty_success(
    db_session: AsyncSession,
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(WINDOW)
    await repository.commit()
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        iss = IssClient(IssConfig(), client=http, should_stop=lambda: bool(requests))
        await ingest._catch_up_ranges(
            repository,
            "stop-iss",
            iss,
            WINDOW[0],
            DAY,
            None,
            frozenset({global_series.SOURCE_ID}),
            should_stop=lambda: bool(requests),
        )
    assert len(requests) == 1
    run = (await db_session.scalars(select(IngestRun))).one()
    assert run.status == "stopped"
    assert (
        await completeness.closed_sessions(repository, GLOBAL, global_series.SOURCE_ID, WINDOW)
        == set()
    )


@pytest.mark.parametrize("response_code", [200, 503])
async def test_cbr_stop_cancels_remaining_requests_and_keeps_partial_rows_unfinished(
    db_session: AsyncSession, response_code: int
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(WINDOW)
    await repository.commit()
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(response_code, text=KEY_RATE_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await ingest._catch_up_ranges(
            repository,
            "stop-cbr",
            Mock(),
            WINDOW[0],
            DAY,
            client,
            frozenset({cbr.SOURCE_ID}),
            should_stop=lambda: bool(requests),
        )
    assert len(requests) == 1
    run = (await db_session.scalars(select(IngestRun))).one()
    assert run.status == "stopped"
    assert run.rows_written == (3 if response_code == 200 else 0)
    assert await completeness.closed_sessions(repository, GLOBAL, cbr.SOURCE_ID, WINDOW) == set()

    # Удачный диапазонный повтор снимает остановку со всех дат, а не только с конца.
    await record(repository, cbr.SOURCE_ID, period=(WINDOW[0], DAY))
    assert await completeness.closed_sessions(repository, GLOBAL, cbr.SOURCE_ID, WINDOW) == set(
        WINDOW
    )


@pytest.mark.parametrize("evidence", ["observation", "successful_range_then_failure"])
async def test_daily_uses_same_completeness_as_report_and_skips_positions(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, evidence: str
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(WINDOW)
    for group in groups.GROUPS:
        if group.has_history:
            for source in group.source_ids:
                if source != cbr.SOURCE_ID:
                    await record(repository, source)
    if evidence == "observation":
        await repository.upsert_global_values(cbr.KEY_RATE_SERIES_ID, {DAY: Decimal("16.5")})
    else:
        await record(repository, cbr.SOURCE_ID, period=(WINDOW[0], DAY))
        await record(repository, cbr.SOURCE_ID, "failed")
    await repository.commit()
    cbr_action = AsyncMock(return_value=verified(cbr.SOURCE_ID, [DAY]))
    positions_action = AsyncMock(return_value=verified(positions.SOURCE_ID, [DAY]))
    links_action = AsyncMock()
    monkeypatch.setattr(advance, "calendar_is_due", AsyncMock(return_value=False))
    monkeypatch.setattr(ingest, "reference_is_due", AsyncMock(return_value=False))
    monkeypatch.setattr(ingest, "_sync_aliases", AsyncMock(return_value=[]))
    monkeypatch.setattr(ingest, "sync_instrument_links", links_action)
    monkeypatch.setattr(ingest, "_sync_cbr", cbr_action)
    monkeypatch.setattr(ingest, "_sync_positions", positions_action)

    assert DAY not in await completeness.closed_sessions(repository, GLOBAL, cbr.SOURCE_ID, [DAY])
    result = await ingest.ingest_session(
        db_session, Settings(), DAY, client=Mock(), positions_client=Mock()
    )
    assert result.succeeded
    cbr_action.assert_awaited_once()
    positions_action.assert_not_awaited()
    links_action.assert_not_awaited()
    assert positions.SOURCE_ID in {outcome.source_id for outcome in result.outcomes}


@pytest.mark.parametrize(
    ("asof", "now", "expected"),
    [
        ("2026-09-18", "2026-09-20T12:00:00+03:00", "2026-09-21"),
        ("2026-09-18", "2026-09-21T10:00:00+03:00", "2026-09-21"),
        ("2026-09-21", "2026-09-21T20:00:00+03:00", "2026-09-22"),
    ],
)
async def test_expected_date_comes_from_worker_and_does_not_skip_uncollected_today(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    asof: str,
    now: str,
    expected: str,
) -> None:
    day = dt.date.fromisoformat(asof)
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([day])
    for group in groups.GROUPS:
        for source in group.source_ids:
            await record(repository, source, day=day)
    await repository.commit()
    monkeypatch.setattr(coverage, "moscow_now", lambda: dt.datetime.fromisoformat(now))
    report = await coverage.build_report(db_session, Settings(), day)
    assert report["next_session"] is None
    assert report["next_expected_session"] == expected
    assert report["next_session_blocked"] is False


async def test_latest_calendar_day_is_already_closed_on_weekend(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = dt.date(2026, 9, 18)
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions([day])
    await repository.commit()
    monkeypatch.setattr(coverage, "moscow_now", lambda: dt.datetime(2026, 9, 20, 12))
    report = await coverage.build_report(db_session, Settings(), day)
    assert report["next_session"] == day.isoformat()
    assert report["next_session_closed"] is True
    assert report["next_expected_session"] is None
