"""Хранение проверенных единиц работы источника (T218)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.db.engine import get_session_factory
from financial_ai.market_data.models import (
    CoverageBoundary,
    GlobalDailySeries,
    IngestRun,
    SourceWorkEvidence,
)
from financial_ai.market_data.repository import MarketDataRepository

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 18)
NOW = dt.datetime(2026, 9, 21, 10, tzinfo=dt.UTC)


async def test_latest_outcomes_cover_only_requested_dates_and_keep_newest_failure(
    db_session: AsyncSession,
) -> None:
    days = [DAY + dt.timedelta(days=offset) for offset in range(5)]
    db_session.add_all(
        [
            IngestRun(
                run_id="range",
                source_id="brent",
                session_date=days[4],
                period_from=days[1],
                period_till=days[3],
                status="ok",
                started_at=NOW,
                finished_at=NOW,
            ),
            IngestRun(
                run_id="failed",
                source_id="brent",
                session_date=days[2],
                status="failed",
                started_at=NOW + dt.timedelta(hours=1),
            ),
            IngestRun(
                run_id="other",
                source_id="cbr",
                session_date=days[0],
                status="ok",
                started_at=NOW,
            ),
        ]
    )
    await db_session.commit()
    repository = MarketDataRepository(db_session)
    requested = [days[4], days[2], days[0], days[1], days[2]]
    latest = await repository.latest_run_by_session(requested, "brent")
    assert {key: run.run_id for key, run in latest.items()} == {
        (days[1], "brent"): "range",
        (days[2], "brent"): "failed",
        (days[4], "brent"): "range",
    }


async def test_evidence_is_unique_and_keeps_original_provenance(
    db_session: AsyncSession,
) -> None:
    repository = MarketDataRepository(db_session)
    assert await repository.record_work_evidence(
        source_id="futures_positions",
        session_date=DAY,
        work_key="EQ_AST_SBER/SBRF_F",
        result_kind="confirmed_absence",
        reason_code="valid_empty_table",
        origin_run_id="first-run",
        verified_at=NOW,
    )
    assert not await repository.record_work_evidence(
        source_id="futures_positions",
        session_date=DAY,
        work_key="EQ_AST_SBER/SBRF_F",
        result_kind="value",
        reason_code="later-repeat",
        origin_run_id="second-run",
        verified_at=NOW + dt.timedelta(hours=1),
    )
    await db_session.commit()

    factory = get_session_factory()
    async with factory() as restarted:
        evidence = await MarketDataRepository(restarted).work_evidence(
            source_id="futures_positions",
            session_date=DAY,
            work_key="EQ_AST_SBER/SBRF_F",
        )
        assert evidence is not None
        assert evidence.result_kind == "confirmed_absence"
        assert evidence.reason_code == "valid_empty_table"
        assert evidence.origin_run_id == "first-run"
        assert evidence.verified_at == NOW


async def test_contract_families_are_distinct_work_units(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)
    for family in ("SBRF_F", "SBRF_M"):
        assert await repository.record_work_evidence(
            source_id="futures_positions",
            session_date=DAY,
            work_key=f"EQ_AST_SBER/{family}",
            result_kind="value",
            reason_code="validated_position_row",
            origin_run_id="family-run",
            verified_at=NOW,
        )
    await db_session.commit()

    evidence = await repository.work_evidence_for_sessions("futures_positions", [DAY])
    assert {row.work_key for row in evidence} == {
        "EQ_AST_SBER/SBRF_F",
        "EQ_AST_SBER/SBRF_M",
    }


async def test_zero_and_null_observations_keep_distinct_evidence(
    db_session: AsyncSession,
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.upsert_global_values("ZERO", {DAY: Decimal("0")})
    await repository.upsert_global_values("NULL", {DAY: None})
    await repository.record_work_evidence(
        source_id="global_series",
        session_date=DAY,
        work_key="ZERO",
        result_kind="value",
        reason_code="observed_zero",
        origin_run_id="values-run",
        verified_at=NOW,
    )
    await repository.record_work_evidence(
        source_id="global_series",
        session_date=DAY,
        work_key="NULL",
        result_kind="value",
        reason_code="observed_null",
        origin_run_id="values-run",
        verified_at=NOW,
    )
    await db_session.commit()

    values = {
        row.series_id: row.value
        for row in (
            await db_session.scalars(
                select(GlobalDailySeries).where(GlobalDailySeries.session_date == DAY)
            )
        ).all()
    }
    evidence = await repository.work_evidence_for_sessions("global_series", [DAY])
    assert values == {"NULL": None, "ZERO": Decimal("0")}
    assert {row.work_key: row.reason_code for row in evidence} == {
        "NULL": "observed_null",
        "ZERO": "observed_zero",
    }


async def test_successful_run_does_not_create_proof_by_itself(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)
    await repository.record_run(
        run_id="unverified-ok",
        source_id="global_series",
        status="ok",
        session_date=DAY,
        rows_written=5,
        started_at=NOW,
        finished_at=NOW,
    )
    await db_session.commit()

    run = await db_session.scalar(select(IngestRun).where(IngestRun.run_id == "unverified-ok"))
    assert run is not None
    assert run.coverage_version is None
    assert run.coverage_reason is None
    assert (
        await db_session.scalar(
            select(SourceWorkEvidence).where(SourceWorkEvidence.source_id == "global_series")
        )
        is None
    )


async def test_version_two_boundary_is_immutable_and_version_one_survives(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            CoverageBoundary(coverage_version=1, boundary_session=DAY - dt.timedelta(days=1)),
            CoverageBoundary(coverage_version=2, boundary_session=DAY),
        ]
    )
    await db_session.commit()

    repository = MarketDataRepository(db_session)
    assert await repository.coverage_boundary() == DAY
    await repository.add_trading_sessions([DAY + dt.timedelta(days=1)])
    await db_session.commit()

    factory = get_session_factory()
    async with factory() as restarted:
        assert await MarketDataRepository(restarted).coverage_boundary() == DAY
        boundaries = (
            await restarted.scalars(
                select(CoverageBoundary).order_by(CoverageBoundary.coverage_version)
            )
        ).all()
        assert [(row.coverage_version, row.boundary_session) for row in boundaries] == [
            (1, DAY - dt.timedelta(days=1)),
            (2, DAY),
        ]


async def test_empty_database_has_no_version_two_boundary(db_session: AsyncSession) -> None:
    assert await MarketDataRepository(db_session).coverage_boundary() is None
