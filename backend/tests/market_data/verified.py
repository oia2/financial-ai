"""Test builders for the explicit coverage-v2 contract."""

from __future__ import annotations

import datetime as dt

from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.verification import required_work_keys


async def record_verified_run(
    repository: MarketDataRepository,
    *,
    run_id: str,
    source_id: str,
    session_date: dt.date,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    rows_written: int = 0,
    trigger: str = "daily",
    period_from: dt.date | None = None,
    period_till: dt.date | None = None,
    evidence_dates: list[dt.date] | None = None,
) -> None:
    """Record a successful run together with every required work proof."""
    await repository.record_run(
        run_id=run_id,
        source_id=source_id,
        status="ok",
        started_at=started_at,
        finished_at=finished_at,
        session_date=session_date,
        rows_written=rows_written,
        trigger=trigger,
        period_from=period_from,
        period_till=period_till,
        coverage_version=2,
        coverage_reason="test_verified_work",
    )
    for day in evidence_dates or [session_date]:
        for work_key in required_work_keys(source_id):
            await repository.record_work_evidence(
                source_id=source_id,
                session_date=day,
                work_key=work_key,
                result_kind="value",
                reason_code="test_verified_work",
                origin_run_id=run_id,
            )
