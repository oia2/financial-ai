"""Контракт состояния сбора и журнала прогонов — contracts/collection-state-api.md.

Проверяется ровно то, чего экрану не хватало и без чего он не может сказать
правду: режим прогона, исход каждой сессии, причина каждого пропуска, план
источников текущей сессии с областью каждого, и журнал, переживающий перезапуск.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data import journal, plan
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.runner import CatchupState, CatchupStatus

pytestmark = pytest.mark.db

SESSIONS = [dt.date(2026, 9, 14), dt.date(2026, 9, 15), dt.date(2026, 9, 16)]


def running_state() -> CatchupState:
    state = CatchupState(
        status=CatchupStatus.RUNNING,
        mode=plan.MODE_DAILY,
        requested=list(SESSIONS),
        date_from=SESSIONS[0],
        date_till=SESSIONS[-1],
        started_at=dt.datetime.now(dt.UTC),
    )
    state.begin_session(SESSIONS[0])
    state.note_source("trading_calendar", "done")
    state.note_source("equity_d1", "done")
    state.note_source("equity_agg", "running", "243 бумаги")
    return state


def test_состояние_называет_режим_и_план_источников() -> None:
    snapshot = running_state().snapshot()

    assert snapshot["mode"] == plan.MODE_DAILY

    current = snapshot["current"]
    assert current is not None
    assert current["session_date"] == SESSIONS[0].isoformat()

    sources = current["sources"]
    # План — весь, а не только отработавшие: видно и то, что впереди.
    assert [row["source_id"] for row in sources] == [
        spec.source_id for spec in plan.for_mode(plan.MODE_DAILY)
    ]
    assert {row["state"] for row in sources} == {"done", "running", "pending"}


def test_область_источника_различает_сессию_период_и_сутки() -> None:
    sources = running_state().snapshot()["current"]["sources"]
    scopes = {row["source_id"]: row["scope"] for row in sources}

    # Календарь идёт раз в сутки и в счёт источников сессии не входит: иначе
    # счётчик обещал бы, что он повторится на следующий день.
    assert scopes["trading_calendar"] == plan.DAILY
    assert scopes["equity_d1"] == plan.SESSION


def test_у_ручного_сбора_свой_план() -> None:
    state = CatchupState(mode=plan.MODE_MANUAL, requested=list(SESSIONS))
    state.begin_session(SESSIONS[0])

    sources = state.snapshot()["current"]["sources"]
    scopes = {row["source_id"]: row["scope"] for row in sources}

    assert len(sources) == len(plan.CATCHUP_PLAN)
    assert scopes["global_series"] == plan.PERIOD
    assert "trading_calendar" not in scopes


def test_исход_каждой_сессии_и_причина_каждого_пропуска() -> None:
    state = running_state()
    state.outcomes[SESSIONS[0]] = "collected"
    state.note_skip(SESSIONS[1], "attempts_exhausted", "5 попыток из 5")

    snapshot = state.snapshot()

    assert snapshot["sessions"]["collected"] == 1
    assert snapshot["sessions"]["skipped"] == 1
    assert snapshot["sessions"]["pending"] == 1
    assert snapshot["skips"] == [
        {
            "session_date": SESSIONS[1].isoformat(),
            "reason": "attempts_exhausted",
            "detail": "5 попыток из 5",
        }
    ]


async def test_журнал_переживает_перезапуск_сборщика(db_session: object) -> None:
    """Итог прогона читается из хранилища, а не из памяти процесса."""
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    started = dt.datetime.now(dt.UTC)

    for source_id, status in (("equity_d1", "ok"), ("brent", "failed")):
        await repository.record_run(
            run_id="run-1",
            source_id=source_id,
            status=status,
            started_at=started,
            finished_at=started + dt.timedelta(seconds=5),
            session_date=SESSIONS[0],
            failure_reason=None if status == "ok" else "источник не ответил вовремя",
        )
    await repository.record_skip(
        session_date=SESSIONS[1],
        reason="retry_delay",
        decided_at=started,
        run_id="run-1",
        detail="повтор через 15 мин",
    )
    await db_session.commit()  # type: ignore[attr-defined]

    runs = await journal.recent_runs(db_session, limit=5)  # type: ignore[arg-type]

    assert len(runs) == 1
    summary = runs[0].to_dict()
    assert summary["mode"] == plan.MODE_DAILY
    assert summary["status"] == journal.STATUS_FAILED
    assert summary["sessions"] == {"requested": 1, "collected": 0, "failed": 1, "skipped": 1}
    assert summary["failures"][0]["source_id"] == "brent"
    assert summary["failures"][0]["title"] == "Brent"


async def test_прогон_без_отметки_завершения_помечается_прерванным(db_session: object) -> None:
    """Перезапуск сборщика не оставляет прогон «идущим» навсегда."""
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    started = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)

    await repository.record_run(
        run_id="run-lost",
        source_id="equity_d1",
        status="ok",
        started_at=started,
        finished_at=None,
        session_date=SESSIONS[0],
    )
    await db_session.commit()  # type: ignore[attr-defined]

    before = await journal.recent_runs(db_session, limit=5)  # type: ignore[arg-type]
    assert before[0].status == journal.STATUS_INTERRUPTED

    marked = await journal.mark_interrupted(db_session, dt.datetime.now(dt.UTC))  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    assert marked == 1
    after = await journal.recent_runs(db_session, limit=5)  # type: ignore[arg-type]
    assert after[0].status != journal.STATUS_INTERRUPTED
    assert after[0].finished_at is not None
