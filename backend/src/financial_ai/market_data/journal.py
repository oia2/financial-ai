"""Журнал прогонов: как прошёл сбор, когда его уже не видно.

Ход работы живёт в памяти процесса намеренно: хранимое «идёт» переживает
падение и блокирует запуск навсегда. Но вместе с процессом исчезал и ответ на
вопрос «как прошло» — человек возвращался в раздел и видел пустоту.

**Новой таблицы для этого не нужно.** Исходы сбора уже пишутся построчно:
прогон, сессия, источник, статус, причина, число строк, начало и конец. Итог
получается свёрткой по ``run_id``, режим берётся из триггера. Заводить вторую
запись того же факта значило бы однажды разойтись с первой (spec 008, FR-005).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data import plan
from financial_ai.market_data.models import AssetFuturesLink, IngestRun, SessionSkip

logger = logging.getLogger(__name__)

# Триггер сбора → режим прогона на языке раздела.
_MODE_BY_TRIGGER = {
    "daily": plan.MODE_DAILY,
    "catchup": plan.MODE_MANUAL,
    "backfill": plan.MODE_MANUAL,
}

STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"

# Причина, которой отмечаются записи прогона, не пережившего перезапуск. По ней
# журнал и узнаёт прерванный прогон: после отметки у записи есть и завершение,
# и статус неудачи, а различие «оборван» против «не удался» должно сохраниться
# (FR-041, FR-052).
INTERRUPTED_REASON = "прогон прерван"


@dataclass(slots=True)
class RunFailure:
    """Источник, не отдавший данные за сессию."""

    source_id: str
    session_date: dt.date | None
    reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "title": plan.title_of(self.source_id),
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "reason": self.reason,
        }


@dataclass(slots=True)
class RunSummary:
    """Итог одного прогона."""

    run_id: str
    mode: str
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: str
    requested: int = 0
    collected: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[RunFailure] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "sessions": {
                "requested": self.requested,
                "collected": self.collected,
                "failed": self.failed,
                "skipped": self.skipped,
            },
            "failures": [failure.to_dict() for failure in self.failures],
        }


@dataclass(slots=True)
class LinkChange:
    """Изменение состава инструментов, увиденное сбором."""

    at: dt.datetime
    ticker: str
    kind: str
    contract_code: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "at": self.at.isoformat(),
            "ticker": self.ticker,
            "kind": self.kind,
            "contract_code": self.contract_code,
            "detail": self.detail,
        }


@dataclass(slots=True)
class SkipRecord:
    """Пропущенная сессия и причина, по которой она пропущена."""

    session_date: dt.date
    reason: str
    detail: str | None
    decided_at: dt.datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "session_date": self.session_date.isoformat(),
            "reason": self.reason,
            "detail": self.detail,
            "decided_at": self.decided_at.isoformat(),
        }


async def count_skips(session: AsyncSession) -> int:
    """Сколько пропусков записано всего.

    Список ограничен, и молчать об остатке нельзя: двадцатая строка иначе
    выглядит последней.
    """
    return int(await session.scalar(select(func.count()).select_from(SessionSkip)) or 0)


async def count_link_events(session: AsyncSession) -> int:
    """Сколько изменений состава записано всего."""
    return int(await session.scalar(select(func.count()).select_from(AssetFuturesLink)) or 0)


async def recent_skips(session: AsyncSession, limit: int = 20) -> list[SkipRecord]:
    """Последние пропуски сессий с причинами.

    Ход прогона живёт в памяти процесса намеренно, а **причина обязана жить
    дольше**: без неё человек видит дыру и не знает, ждать ему или вмешиваться.
    Таблица пропусков писалась с самого начала фичи и не читалась ниоткуда —
    причина исчезала вместе с перезапуском сборщика, оставляя один счётчик
    (FR-002, SC-002).

    Чтение этой таблицы **одно**, и живёт оно здесь, рядом с прочим журналом:
    второй читатель той же таблицы однажды разошёлся бы с первым в том, какую
    из причин сессии считать действующей.
    """
    limit = max(1, min(limit, 100))

    rows = (
        await session.execute(
            select(SessionSkip).order_by(SessionSkip.decided_at.desc()).limit(limit)
        )
    ).scalars()

    seen: set[dt.date] = set()
    records: list[SkipRecord] = []
    for row in rows:
        # На сессию причин может быть несколько — берётся последняя: она и
        # описывает нынешнее положение дел.
        if row.session_date in seen:
            continue
        seen.add(row.session_date)
        records.append(
            SkipRecord(
                session_date=row.session_date,
                reason=row.reason,
                detail=row.detail,
                decided_at=row.decided_at,
            )
        )
    return records


async def recent_link_events(session: AsyncSession, limit: int = 10) -> list[LinkChange]:
    """Последние изменения связей бумаг и контрактов.

    **Отдельной таблицы событий нет намеренно.** События — это и есть интервалы
    связи: первый интервал бумаги означает появление фьючерса, следующий —
    смену семейства, закрытый без продолжения — исчезновение инструмента.
    Вторая запись того же факта однажды разошлась бы с первой (FR-016).
    """
    limit = max(1, min(limit, 50))

    rows = (
        await session.execute(
            select(AssetFuturesLink).order_by(AssetFuturesLink.recorded_at.desc()).limit(limit)
        )
    ).scalars()

    links = list(rows)
    if not links:
        return []

    first_rows = (
        await session.execute(
            select(
                AssetFuturesLink.asset_id,
                func.min(AssetFuturesLink.valid_from).label("valid_from"),
            )
            .where(AssetFuturesLink.asset_id.in_({row.asset_id for row in links}))
            .group_by(AssetFuturesLink.asset_id)
        )
    ).all()
    firsts: dict[str, dt.date] = {row.asset_id: row.valid_from for row in first_rows}

    events: list[LinkChange] = []
    for row in links:
        ticker = row.asset_id.removeprefix("EQ_AST_")
        if row.valid_till is not None and row.valid_till < row.valid_from:
            # Интервал закрыт тем же днём, каким открыт: контракта не стало.
            kind, detail = "closed", f"контракта {row.contract_code} больше нет"
        elif firsts.get(row.asset_id) == row.valid_from:
            kind, detail = "opened", f"появился фьючерс {row.contract_code}"
        else:
            kind, detail = "changed", f"контракт сменился на {row.contract_code}"
        events.append(
            LinkChange(
                at=row.recorded_at,
                ticker=ticker,
                kind=kind,
                contract_code=row.contract_code,
                detail=detail,
            )
        )
    return events


async def recent_runs(session: AsyncSession, limit: int = 5) -> list[RunSummary]:
    """Последние прогоны, от свежих к старым.

    Прогон без отметки завершения считается прерванным: довести его было
    некому. Так состояние «идёт» не висит вечно после перезапуска сборщика.
    """
    limit = max(1, min(limit, 20))

    heads = (
        select(
            IngestRun.run_id,
            func.min(IngestRun.started_at).label("started_at"),
            func.max(IngestRun.finished_at).label("finished_at"),
            func.min(IngestRun.trigger).label("trigger"),
            func.count(func.distinct(IngestRun.session_date)).label("sessions"),
            # Запись ИДУЩЕГО обращения прогон прерванным не делает: исход
            # заводится до обращения и дополняется после (FR-052), и без этого
            # отбора идущий прогон показывался бы «прерванным перезапуском»
            # ровно пока он идёт. Оборванную вместе с процессом запись
            # размечает `mark_interrupted` при следующем запуске.
            func.count(IngestRun.id)
            .filter(IngestRun.finished_at.is_(None), IngestRun.status != "running")
            .label("unfinished"),
        )
        .group_by(IngestRun.run_id)
        .order_by(func.min(IngestRun.started_at).desc())
        .limit(limit)
    )
    rows = (await session.execute(heads)).all()
    if not rows:
        return []

    run_ids = [row.run_id for row in rows]

    failures_rows = (
        await session.execute(
            select(
                IngestRun.run_id,
                IngestRun.source_id,
                IngestRun.session_date,
                IngestRun.failure_reason,
            ).where(IngestRun.run_id.in_(run_ids), IngestRun.status.in_(("failed", "stopped")))
        )
    ).all()

    # Прерванный источник считается наравне с упавшим: сессия по нему не
    # собрана. Прежде учитывались только неудачи, и остановленный прогон
    # показывался завершённым с собранной сессией — при том что спрошены были
    # три бумаги из ста двадцати (FR-050).
    failed_sessions: dict[str, set[dt.date]] = {}
    failures: dict[str, list[RunFailure]] = {}
    for run_id, source_id, session_date, reason in failures_rows:
        failures.setdefault(run_id, []).append(RunFailure(source_id, session_date, reason))
        if session_date is not None:
            failed_sessions.setdefault(run_id, set()).add(session_date)

    skips_rows = (
        await session.execute(
            select(SessionSkip.run_id, func.count())
            .where(SessionSkip.run_id.in_(run_ids))
            .group_by(SessionSkip.run_id)
        )
    ).all()
    skips: dict[str | None, int] = {row[0]: int(row[1]) for row in skips_rows}

    summaries: list[RunSummary] = []
    for row in rows:
        failed = len(failed_sessions.get(row.run_id, set()))
        status = (
            STATUS_INTERRUPTED
            if row.unfinished
            else (STATUS_FAILED if failed and failed == row.sessions else STATUS_FINISHED)
        )
        summaries.append(
            RunSummary(
                run_id=row.run_id,
                mode=_MODE_BY_TRIGGER.get(row.trigger, plan.MODE_DAILY),
                started_at=row.started_at,
                finished_at=None if row.unfinished else row.finished_at,
                status=status,
                requested=row.sessions,
                collected=max(row.sessions - failed, 0),
                failed=failed,
                skipped=int(skips.get(row.run_id, 0)),
                failures=failures.get(row.run_id, []),
            )
        )
    return summaries


async def mark_interrupted(session: AsyncSession, process_started_at: dt.datetime) -> int:
    """Пометить прерванными прогоны, начатые до запуска этого процесса.

    Прогон без отметки завершения, начатый раньше текущего процесса, доведён
    быть не мог: тот, кто его вёл, больше не существует. Без этой отметки он
    остался бы «идущим» навсегда, а незакрытые сессии — непонятно чьими
    (spec 008, FR-041).
    """
    result = await session.execute(
        update(IngestRun)
        .where(IngestRun.finished_at.is_(None), IngestRun.started_at < process_started_at)
        .values(
            finished_at=process_started_at,
            status="failed",
            failure_reason=INTERRUPTED_REASON,
        )
    )
    count = int(getattr(result, "rowcount", 0) or 0)
    if count:
        logger.info("журнал: прерванных прогоном записей помечено %d", count)
    return count
