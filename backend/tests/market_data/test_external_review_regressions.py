"""Регрессии внешнего ревью 2026-09-23 (spec 008, Phase 53).

Каждый тест воспроизводит подтверждённый по коду сценарий: FR-032j, FR-033k–n.
Сеть не используется.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import advance, ingest, plan
from financial_ai.market_data.calendar import MOSCOW
from financial_ai.market_data.models import IngestSessionOutcome
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import cbr, equity_d1, global_series, positions, securities
from financial_ai.market_data.sources.reference import ReferenceEmptyError
from financial_ai.market_data.verification import VerificationResult, WorkEvidence
from tests.market_data.test_catchup import SESSIONS, FakeIss

pytestmark = pytest.mark.db

DAY = SESSIONS[-1]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- FR-032j: предел попыток расходуют только отказы ----------------------------


async def test_gradual_publication_does_not_exhaust_session_attempts(
    db_session: AsyncSession,
) -> None:
    """Шесть прогонов, каждый дособрал часть, позиции ещё не опубликованы.

    Прежде каждый такой прогон считался попыткой, и предел 5 исчерпывался
    раньше, чем выходили позиции. Остановка человеком и обрыв перезапуском
    тоже не отказы. Отказ источника и сбой обработки — попытки.
    """
    repository = MarketDataRepository(db_session)
    moment = _now()
    progress = [equity_d1.SOURCE_ID, "equity_agg", "brent", "global_series", "cbr", "brent"]
    for index, source_id in enumerate(progress):
        run_id = f"progress-{index}"
        await repository.record_run(
            run_id=run_id,
            source_id=source_id,
            status="ok",
            started_at=moment,
            finished_at=moment,
            session_date=DAY,
        )
        await repository.record_run(
            run_id=run_id,
            source_id=positions.SOURCE_ID,
            status="failed",
            started_at=moment,
            finished_at=moment,
            session_date=DAY,
            failure_kind=plan.FAILURE_UNPUBLISHED,
        )
    for run_id, kind in (("stopped", plan.FAILURE_STOPPED), ("lost", plan.FAILURE_INTERRUPTED)):
        await repository.record_run(
            run_id=run_id,
            source_id="brent",
            status="failed",
            started_at=moment,
            finished_at=moment,
            session_date=DAY,
            failure_kind=kind,
        )
    await db_session.commit()

    assert await repository.attempts_by_session([DAY]) == {}

    for run_id, kind in (("refused", plan.FAILURE_SOURCE), ("bug", plan.FAILURE_INTERNAL)):
        await repository.record_run(
            run_id=run_id,
            source_id="brent",
            status="failed",
            started_at=moment,
            finished_at=moment,
            session_date=DAY,
            failure_kind=kind,
        )
    # Старая запись без причины — отказ источника, как её читает сводка.
    await repository.record_run(
        run_id="legacy",
        source_id="brent",
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=DAY,
    )
    await db_session.commit()

    assert await repository.attempts_by_session([DAY]) == {DAY: 3}


# --- FR-033k: справочник повторяется и без сессионной работы --------------------


async def test_unverified_reference_is_retried_after_delay(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)
    settings = Settings(market_data_retry_after_minutes=15)
    now = _now()
    await repository.record_run(
        run_id="sectors-ok",
        source_id="equity_sectors",
        status="ok",
        started_at=now,
        finished_at=now,
        coverage_version=2,
        coverage_reason="verified_work_evidence",
    )
    await repository.record_run(
        run_id="lots-failed",
        source_id=securities.SOURCE_ID,
        status="failed",
        started_at=now - dt.timedelta(minutes=5),
        finished_at=now - dt.timedelta(minutes=5),
        failure_kind=plan.FAILURE_SOURCE,
    )
    await db_session.commit()

    # Отрасли проверены сегодня; лоты упали пять минут назад — ждут выдержки.
    assert await ingest.references_to_retry(repository, settings, now) == frozenset()
    # Выдержка прошла — ежедневный цикл спросит лоты сам.
    later = now + dt.timedelta(minutes=11)
    assert await ingest.references_to_retry(repository, settings, later) == {securities.SOURCE_ID}


async def test_daily_cycle_refreshes_reference_without_session_work(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Окно полное, справочник упал: цикл спрашивает его, а не ждёт сессии.

    Прежде справочники спрашивал только сбор сессии, и ML ждал следующего дня.
    """
    monkeypatch.setattr(advance, "calendar_is_due", AsyncMock(return_value=False))
    monkeypatch.setattr(advance, "pending_sessions", AsyncMock(return_value=([], DAY)))
    monkeypatch.setattr(
        ingest, "references_to_retry", AsyncMock(return_value=frozenset({securities.SOURCE_ID}))
    )
    refresh = AsyncMock(return_value=[])
    monkeypatch.setattr(ingest, "refresh_references", refresh)

    result = await advance.advance(
        db_session, Settings(), dt.datetime(2026, 9, 23, 20, tzinfo=MOSCOW)
    )

    assert result.collected == []
    refresh.assert_awaited_once()
    assert refresh.await_args.args[2] == {securities.SOURCE_ID}
    assert refresh.await_args.kwargs["trigger"] == ingest.TRIGGER_DAILY


# --- FR-033l: пустой перечень лотов — отказ -----------------------------------


async def test_empty_lot_list_is_a_source_failure(db_session: AsyncSession) -> None:
    class EmptyLots:
        async def fetch_equity_lot_sizes(self) -> dict[str, int]:
            return {}

    with pytest.raises(ReferenceEmptyError):
        await securities.sync_lot_sizes(EmptyLots(), MarketDataRepository(db_session))  # type: ignore[arg-type]


# --- FR-033m: пустая база за один проход ---------------------------------------


async def test_empty_store_builds_links_from_collected_board(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Связи — по доске, котировки которой уже записаны; справочники — после сессий.

    Прежде на пустом хранилище связи строились до котировок: состав доски
    пуст, связей 0, запросов позиций 0, лоты NULL при «проверенном» справочнике.
    """
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(SESSIONS)
    await db_session.commit()
    order: list[str] = []

    async def fake_links(repository, iss, session_date, alias_events=None, traded_on=None):  # type: ignore[no-untyped-def]
        order.append("links")
        assert traded_on == DAY
        assert await repository.assets_traded_on(DAY) == {"EQ_AST_SBER"}

    async def fake_positions(settings, iss, repository, day, *args, **kwargs):  # type: ignore[no-untyped-def]
        order.append(f"positions:{day}")
        return VerificationResult(
            rows_written=0, evidence=(WorkEvidence(day, "applicable_links", "not_applicable"),)
        )

    async def fake_references(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("references")
        return []

    monkeypatch.setattr(ingest, "sync_instrument_links", fake_links)
    monkeypatch.setattr(ingest, "_sync_positions", fake_positions)
    monkeypatch.setattr(ingest, "refresh_references", fake_references)
    iss = FakeIss()

    await ingest.catch_up(
        db_session,
        Settings(market_data_catchup_window_sessions=5, market_data_positions_window_sessions=5),
        DAY,
        client=iss,
        cbr_client=cbr_client,
        positions_client=object(),  # type: ignore[arg-type]
        sessions=list(SESSIONS),
        source_ids=frozenset(
            {equity_d1.SOURCE_ID, positions.SOURCE_ID, "equity_sectors", securities.SOURCE_ID}
        ),
        run_id="empty-store",
    )

    assert order[0] == "links"
    assert [item for item in order if item.startswith("positions")] == [
        f"positions:{day}" for day in SESSIONS
    ]
    assert order[-1] == "references"
    # Котировки последней сессии, собранные до связей, в цикле повторно не спрашиваются.
    assert iss.quote_calls.count(DAY.isoformat()) == 1


# --- FR-033n: итог даты диапазонного источника по доказательствам --------------


async def test_journal_keeps_proved_range_date_collected(
    db_session: AsyncSession, cbr_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Две даты: первая доказана, вторая нет — журнал не называет обе несобранными."""
    first, second = SESSIONS[-2], SESSIONS[-1]
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(SESSIONS)
    await db_session.commit()

    async def iss_range(*args, **kwargs):  # type: ignore[no-untyped-def]
        return VerificationResult(
            rows_written=5,
            evidence=tuple(
                WorkEvidence(first, spec.series_id) for spec in global_series.ISS_SERIES
            ),
            complete=False,
            failure_kind=plan.FAILURE_SOURCE,
        )

    async def cbr_range(*args, **kwargs):  # type: ignore[no-untyped-def]
        return VerificationResult(
            rows_written=2,
            evidence=tuple(
                WorkEvidence(day, key)
                for day in (first, second)
                for key in (cbr.KEY_RATE_SERIES_ID, "CBR_ZCYC_CURVE")
            ),
        )

    monkeypatch.setattr(global_series, "sync_iss_series_range", iss_range)
    monkeypatch.setattr(ingest, "_sync_cbr_range", cbr_range)

    await ingest.catch_up(
        db_session,
        Settings(market_data_catchup_window_sessions=5),
        second,
        client=FakeIss(),
        cbr_client=cbr_client,
        positions_client=object(),  # type: ignore[arg-type]
        sessions=[first, second],
        source_ids=frozenset({global_series.SOURCE_ID, cbr.SOURCE_ID}),
        run_id="range-journal",
        prepare_assets=False,
    )

    saved = {
        row.session_date: row.outcome
        for row in await db_session.scalars(
            select(IngestSessionOutcome).where(IngestSessionOutcome.run_id == "range-journal")
        )
    }
    assert saved[first] == plan.OUTCOME_COLLECTED
    assert saved[second] != plan.OUTCOME_COLLECTED


async def test_reference_waits_out_the_retry_inside_session_collection(
    db_session: AsyncSession,
) -> None:
    """Выдержка после неудачи — и внутри сбора сессии: при догоне N сессий упавший
    справочник спрашивался N раз подряд (FR-055a)."""
    repository = MarketDataRepository(db_session)
    failed_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    await repository.record_run(
        run_id="lots-failed",
        source_id=securities.SOURCE_ID,
        status="failed",
        started_at=failed_at,
        finished_at=failed_at,
        failure_kind=plan.FAILURE_SOURCE,
    )
    await db_session.commit()

    waiting = Settings(market_data_retry_after_minutes=15)
    assert not await ingest._reference_wanted(repository, securities.SOURCE_ID, DAY, waiting)
    elapsed = Settings(market_data_retry_after_minutes=1)
    assert await ingest._reference_wanted(repository, securities.SOURCE_ID, DAY, elapsed)
