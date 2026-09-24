"""Счёт сводки в базе совпадает с прежним счётом в Python (spec 008, T280).

Сводка перестала поднимать журнал исходов и строки таблиц целиком: последний
исход пары и числа строк по сессиям считает база. Ответ обязан остаться тем же —
иначе ускорение поменяло бы то, что видит человек. Поэтому здесь новые запросы
сверяются с прежними реализациями на данных с подвохами: прогон за диапазон,
несколько попыток за одну дату, одинаковое время завершения, бумага без вида.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.models import EquityDailyBar
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

DAYS = [dt.date(2026, 9, 14), dt.date(2026, 9, 15), dt.date(2026, 9, 16), dt.date(2026, 9, 17)]


def _at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, 18, hour, minute, tzinfo=dt.UTC)


async def _run(
    repository: MarketDataRepository,
    run_id: str,
    source_id: str,
    status: str,
    finished: dt.datetime | None,
    *,
    day: dt.date | None = None,
    period: tuple[dt.date, dt.date] | None = None,
    reason: str | None = None,
    kind: str | None = None,
) -> None:
    await repository.record_run(
        run_id=run_id,
        source_id=source_id,
        status=status,
        started_at=(finished or _at(23)) - dt.timedelta(minutes=1),
        finished_at=finished,
        session_date=day,
        period_from=period[0] if period else None,
        period_till=period[1] if period else None,
        failure_reason=reason,
        failure_kind=kind,
    )


async def test_latest_unfinished_runs_match_the_python_rule(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(DAYS)

    # Диапазон упал на весь период, затем одна дата собрана посессионно.
    await _run(
        repository,
        "r1",
        "cbr",
        "failed",
        _at(10),
        period=(DAYS[0], DAYS[-1]),
        reason="ЦБ не ответил",
        kind="source",
    )
    await _run(repository, "r2", "cbr", "ok", _at(11), day=DAYS[1])
    # Неудача, затем успех; и успех, затем неудача — побеждает последний.
    await _run(repository, "r3", "equity_d1", "failed", _at(9), day=DAYS[0], kind="source")
    await _run(repository, "r4", "equity_d1", "ok", _at(12), day=DAYS[0])
    await _run(repository, "r5", "equity_d1", "ok", _at(9), day=DAYS[2])
    await _run(
        repository,
        "r6",
        "equity_d1",
        "stopped",
        _at(12),
        day=DAYS[2],
        reason="остановлено",
        kind="stopped",
    )
    # Идущее обращение без отметки завершения и одинаковое время двух записей.
    await _run(repository, "r7", "brent", "running", None, day=DAYS[3])
    await _run(repository, "r8", "index_constituents", "ok", _at(8), day=DAYS[3])
    await _run(
        repository,
        "r9",
        "index_constituents",
        "failed",
        _at(8),
        day=DAYS[3],
        reason="тот же момент, запись позже",
        kind="source",
    )
    await db_session.commit()

    expected = {
        key: (run.status, run.failure_reason, run.failure_kind)
        for key, run in (await repository.latest_run_by_session(DAYS)).items()
        if run.status in {"failed", "stopped", "running"}
    }
    actual = {
        key: (run.status, run.failure_reason, run.failure_kind)
        for key, run in (await repository.latest_unfinished_runs(DAYS)).items()
    }

    assert actual == expected
    # Правило видно и без сравнения: DAYS[1] у ЦБ закрыт поздним успехом.
    assert (DAYS[1], "cbr") not in actual
    assert actual[(DAYS[0], "cbr")][0] == "failed"
    assert actual[(DAYS[2], "equity_d1")][0] == "stopped"
    assert actual[(DAYS[3], "brent")][0] == "running"
    assert actual[(DAYS[3], "index_constituents")][0] == "failed"


def _bar(ticker: str, day: dt.date, close: str | None) -> DailyBar:
    value = None if close is None else Decimal(close)
    return DailyBar(
        asset_id=f"EQ_AST_{ticker}",
        price_series_id=f"EQ_PRS_{ticker}",
        session_date=day,
        open=value,
        high=value,
        low=value,
        close=value,
        volume=None if value is None else Decimal(10),
    )


async def test_rows_by_session_add_up_to_group_coverage(db_session: AsyncSession) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(DAYS)
    for ticker in ("SBER", "TBEU", "NEWX"):
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, DAYS[0])
        await repository.upsert_price_series(f"EQ_PRS_{ticker}", f"EQ_AST_{ticker}", DAYS[0])
    await repository.update_security_kinds({"EQ_AST_SBER": "share", "EQ_AST_TBEU": "fund"})
    # NEWX без вида — считается к акциям, как в прежнем счёте.
    await repository.upsert_daily_bars(
        [_bar("SBER", day, "300") for day in DAYS]
        + [_bar("TBEU", day, None if day == DAYS[2] else "1.5") for day in DAYS[1:]]
        + [_bar("NEWX", DAYS[3], None)]
    )
    await db_session.commit()

    by_day = await repository.rows_by_session(
        EquityDailyBar, "session_date", ("close",), DAYS, split_by_kind=True
    )
    window = DAYS[1:]
    for kind, is_fund in (("share", False), ("fund", True)):
        raw = await repository.group_coverage(
            EquityDailyBar, "session_date", ("close",), window, kind
        )
        picked = {
            day: counts
            for (fund, day), counts in by_day.items()
            if fund == is_fund and day in window
        }
        assert sum(total for total, _ in picked.values()) == raw.rows_total
        assert sum(filled for _, filled in picked.values()) == raw.rows_with_values
        assert len(picked) == raw.sessions_covered
        assert min(picked) == raw.period_from
        assert max(picked) == raw.period_till
