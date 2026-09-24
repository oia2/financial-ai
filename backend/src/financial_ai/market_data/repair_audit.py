"""Чтение состояния для ручного ремонта рыночных данных.

Аудит не пишет в БД и не делает внешних запросов. Его результат развёрнут до
пары «источник — сессия»: сумма строк глобальной таблицы не подтверждает
отдельно ни ЦБ, ни Brent, ни состав индекса.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
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

ISS_CURSOR_INCONSISTENCY = "iss_contract_cursor_inconsistency"


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
    # Источник проверяется один раз: у котировок и агрегатов по две группы —
    # акций и фондов, — а работа источника одна (FR-060a).
    audited: set[str] = set()
    for group in groups.GROUPS:
        if not group.has_history or group.session_column is None:
            continue
        closed_by_source = await completeness.closed_by_source(repository, group, sessions)
        for source_id in group.source_ids:
            if source_ids is not None and source_id not in source_ids:
                continue
            if source_id in audited:
                continue
            audited.add(source_id)
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


async def run_plan(
    session: AsyncSession,
    settings: Settings,
    plan_id: str,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[str, int, int]:
    """Run a persisted repair plan only while this process owns collection."""
    ownership = MarketDataRunLock()
    await ownership.acquire()
    try:
        return await _run_plan_owned(session, settings, plan_id, should_stop=should_stop)
    finally:
        await ownership.release()


async def _run_plan_owned(
    session: AsyncSession,
    settings: Settings,
    plan_id: str,
    *,
    should_stop: Callable[[], bool] | None = None,
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
    # Completed is terminal even if a newer coverage rule would judge its
    # historical input differently.  Check it before creating any client.
    if plan.status == "completed":
        return plan.status, plan.requests_spent, 0
    if plan.status == "running":
        await repository.finish_repair_plan(plan_id, "stopped", "interrupted")
    pending = [
        item
        for item in items
        if item.status == "pending" and getattr(item, "reason", None) != ISS_CURSOR_INCONSISTENCY
    ]
    # A data transaction can commit immediately before the marker above.  A
    # cold restart discovers that fact from the exact pair, preserving both
    # idempotence and the original HTTP budget.
    still_pending = []
    for item in pending:
        row = next(
            (
                row
                for row in await audit(
                    repository,
                    item.session_date,
                    item.session_date,
                    frozenset({item.source_id}),
                )
                if row.source_id == item.source_id and row.session_date == item.session_date
            ),
            None,
        )
        if row is not None and not row.unknown:
            await repository.finish_repair_item(
                plan_id, item.session_date, item.source_id, "already_closed"
            )
        else:
            still_pending.append(item)
    pending = still_pending
    if not pending:
        return plan.status, plan.requests_spent, 0
    if plan.requests_spent >= plan.request_budget:
        await repository.finish_repair_plan(plan_id, "stopped", "request_budget_exhausted")
        return "stopped", plan.requests_spent, len(pending)
    if should_stop is not None and should_stop():
        await repository.finish_repair_plan(plan_id, "stopped", "operator_stopped")
        return "stopped", plan.requests_spent, len(pending)

    exhausted = False
    stopped = False

    def stopping() -> bool:
        return exhausted or (should_stop is not None and should_stop())

    async def permit() -> None:
        nonlocal exhausted
        if stopping():
            detail = "request_budget_exhausted" if exhausted else "operator_stopped"
            raise SourceStoppedError(detail=detail)
        if not await repository.reserve_repair_attempt(plan_id):
            exhausted = True
            raise SourceStoppedError(detail="request_budget_exhausted")

    async def cbr_permit(_: httpx.Request) -> None:
        await permit()

    # Do not pass the two sets to catch_up together: that turns a sparse plan
    # into their Cartesian product.  One persisted item is one collection
    # request scope; range sources may consequently choose a one-day range.
    async with (
        IssClient(
            ingest.build_iss_config(settings), should_stop=stopping, request_permit=permit
        ) as iss,
        PositionsClient(settings, should_stop=stopping, request_permit=permit) as positions,
        httpx.AsyncClient(event_hooks={"request": [cbr_permit]}) as cbr_client,
    ):
        try:
            for item in pending:
                if stopping():
                    stopped = True
                    break
                await ingest.catch_up(
                    session,
                    settings,
                    item.session_date,
                    client=iss,
                    cbr_client=cbr_client,
                    positions_client=positions,
                    sessions=[item.session_date],
                    source_ids=frozenset({item.source_id}),
                    should_stop=stopping,
                    run_id=plan_id,
                    prepare_assets=item.source_id
                    in {"equity_d1", "equity_agg", "futures_positions"},
                )
                row = next(
                    (
                        row
                        for row in await audit(
                            repository,
                            item.session_date,
                            item.session_date,
                            frozenset({item.source_id}),
                        )
                        if row.source_id == item.source_id and row.session_date == item.session_date
                    ),
                    None,
                )
                if row is not None and not row.unknown:
                    await repository.finish_repair_item(
                        plan_id, item.session_date, item.source_id, "closed"
                    )
        except SourceStoppedError:
            exhausted = not (should_stop is not None and should_stop())
            stopped = not exhausted

    refreshed = await repository.repair_plan(plan_id)
    assert refreshed is not None
    current, current_items = refreshed
    remaining = sum(item.status == "pending" for item in current_items)
    if exhausted:
        status, reason = "stopped", "request_budget_exhausted"
    elif stopped:
        status, reason = "stopped", "operator_stopped"
    elif remaining:
        status, reason = "incomplete", "unresolved_pairs_remain"
    else:
        status, reason = "completed", None
    await repository.finish_repair_plan(plan_id, status, reason)
    return status, current.requests_spent, remaining


async def extend_plan(repository: MarketDataRepository, plan_id: str, request_budget: int) -> None:
    if request_budget <= 0 or not await repository.extend_repair_plan(plan_id, request_budget):
        raise ValueError("новый лимит должен быть больше текущего лимита существующего плана")
