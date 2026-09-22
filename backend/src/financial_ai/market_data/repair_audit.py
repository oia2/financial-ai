"""Чтение состояния для ручного ремонта рыночных данных.

Аудит не пишет в БД и не делает внешних запросов. Его результат развёрнут до
пары «источник — сессия»: сумма строк глобальной таблицы не подтверждает
отдельно ни ЦБ, ни Brent, ни состав индекса.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, groups
from financial_ai.market_data.interrupt import SourceStoppedError
from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.lock import MarketDataRunLock
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.positions_client import PositionsClient


@dataclass(frozen=True, slots=True)
class AuditRow:
    session_date: dt.date
    source_id: str
    required: bool
    present: bool
    confirmed_absence: bool
    unknown: bool


async def audit(
    repository: MarketDataRepository,
    date_from: dt.date,
    date_till: dt.date,
    source_ids: frozenset[str] | None = None,
) -> list[AuditRow]:
    """Вернуть доказуемый статус каждого исторического источника и сессии."""
    sessions = await repository.sessions_between(date_from, date_till)
    boundary = await repository.coverage_boundary()
    result: list[AuditRow] = []
    for group in groups.GROUPS:
        if not group.has_history or group.session_column is None:
            continue
        closed_by_source = await completeness.closed_by_source(repository, group, sessions)
        for source_id in group.source_ids:
            if source_ids is not None and source_id not in source_ids:
                continue
            present = await repository.sessions_with_observations(
                group.model,
                group.session_column,
                group.value_columns,
                sessions,
                group.key_column,
                group.keys_of(source_id),
            )
            audit_required = await completeness.requires_audit_sessions(
                repository,
                group,
                source_id,
                sessions,
                closed=closed_by_source[source_id],
                boundary=boundary,
            )
            closed = closed_by_source[source_id]
            for day in sessions:
                result.append(
                    AuditRow(
                        session_date=day,
                        source_id=source_id,
                        required=True,
                        present=day in present,
                        confirmed_absence=day in closed and day not in present,
                        unknown=day not in closed or day in audit_required,
                    )
                )
    return result


async def create_plan(
    repository: MarketDataRepository,
    date_from: dt.date,
    date_till: dt.date,
    request_budget: int,
    source_ids: frozenset[str] | None = None,
) -> tuple[str, list[AuditRow]]:
    """Сохранить только неизвестные пары; сам план ничего не собирает."""
    if request_budget <= 0:
        raise ValueError("лимит HTTP-попыток должен быть положительным")
    rows = await audit(repository, date_from, date_till, source_ids)
    plan_id = str(uuid.uuid4())
    # Brent идёт первым: это доказанный случай и один ISS-запрос на страницу.
    rows.sort(key=lambda row: (row.source_id != "brent", row.session_date, row.source_id))
    await repository.create_repair_plan(
        plan_id,
        request_budget,
        [(row.session_date, row.source_id, 1) for row in rows if row.unknown],
    )
    return plan_id, rows


async def run_plan(session: AsyncSession, settings: Settings, plan_id: str) -> tuple[str, int, int]:
    """Run a persisted repair plan only while this process owns collection."""
    ownership = MarketDataRunLock()
    await ownership.acquire()
    try:
        return await _run_plan_owned(session, settings, plan_id)
    finally:
        await ownership.release()


async def _run_plan_owned(
    session: AsyncSession, settings: Settings, plan_id: str
) -> tuple[str, int, int]:
    """Run exactly one explicit repair plan, preserving its HTTP budget.

    The existing catch-up path remains the sole source collector.  This wrapper
    only narrows it to persisted unknown pairs and guards every physical HTTP
    request (including retries, probes, and pagination).
    """
    # Keeping the public boundary small avoids a second implementation of
    # collection semantics.  The concrete types are intentionally imported
    # lazily here to avoid circular imports with ingest.
    from financial_ai.market_data import ingest

    repository = MarketDataRepository(session)
    saved = await repository.repair_plan(plan_id)
    if saved is None:
        raise ValueError(f"план ремонта {plan_id} не найден")
    plan, items = saved
    # A process that died mid-plan leaves no worker to finish it.  Preserve
    # the consumed budget and make the interruption explicit before resuming.
    if plan.status == "running":
        await repository.finish_repair_plan(plan_id, "stopped", "interrupted")
    pending = [item for item in items if item.status == "pending"]
    if not pending:
        return plan.status, plan.requests_spent, 0
    if plan.requests_spent >= plan.request_budget:
        await repository.finish_repair_plan(plan_id, "stopped", "request_budget_exhausted")
        return "stopped", plan.requests_spent, len(pending)

    exhausted = False

    async def permit() -> None:
        nonlocal exhausted
        if not await repository.reserve_repair_attempt(plan_id):
            exhausted = True
            raise SourceStoppedError(detail="request_budget_exhausted")

    async def cbr_permit(_: httpx.Request) -> None:
        await permit()

    days = sorted({item.session_date for item in pending})
    source_ids = frozenset(item.source_id for item in pending)
    repair_needs_assets = bool(
        source_ids & frozenset({"equity_d1", "equity_agg", "futures_positions"})
    )
    async with (
        IssClient(ingest.build_iss_config(settings), request_permit=permit) as iss,
        PositionsClient(settings, request_permit=permit) as positions,
        httpx.AsyncClient(event_hooks={"request": [cbr_permit]}) as cbr_client,
    ):
        try:
            await ingest.catch_up(
                session,
                settings,
                days[-1],
                client=iss,
                cbr_client=cbr_client,
                positions_client=positions,
                sessions=days,
                source_ids=source_ids,
                should_stop=lambda: exhausted,
                run_id=plan_id,
                prepare_assets=repair_needs_assets,
            )
        except SourceStoppedError:
            exhausted = True

    refreshed = await repository.repair_plan(plan_id)
    assert refreshed is not None
    current, _ = refreshed
    rows = await audit(repository, days[0], days[-1])
    remaining = sum(row.unknown for row in rows if row.source_id in source_ids)
    if exhausted:
        status, reason = "stopped", "request_budget_exhausted"
    elif remaining:
        status, reason = "incomplete", "unresolved_pairs_remain"
    else:
        status, reason = "completed", None
    await repository.finish_repair_plan(plan_id, status, reason)
    return status, current.requests_spent, remaining


async def extend_plan(repository: MarketDataRepository, plan_id: str, request_budget: int) -> None:
    if request_budget <= 0 or not await repository.extend_repair_plan(plan_id, request_budget):
        raise ValueError("новый лимит должен быть больше текущего лимита существующего плана")
