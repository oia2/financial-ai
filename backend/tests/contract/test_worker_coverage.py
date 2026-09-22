"""Контракт GET /internal/coverage — contracts/coverage-report.md.

Отчёт о полноте, а не просмотр данных: конкретных значений наблюдений в ответе
нет и быть не должно.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.repository import DailyBar, MarketDataRepository, PositionRow

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
]
ASOF = SESSIONS[-1]


async def test_concurrent_coverage_requests_share_one_report_build() -> None:
    """Повтор интерфейса не должен запускать второй тяжёлый отчёт."""
    from financial_ai.worker.routes.coverage import CoverageReportFlight

    flight = CoverageReportFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def build() -> dict[str, int]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"calls": calls}

    first = asyncio.create_task(flight.run("latest", build))
    await started.wait()
    second = asyncio.create_task(flight.run("latest", build))
    await asyncio.sleep(0)

    assert calls == 1

    release.set()
    assert await first == {"calls": 1}
    assert await second == {"calls": 1}


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("312.4"),
        high=Decimal("315.1"),
        low=Decimal("311.0"),
        close=Decimal("314.22"),
        volume=Decimal("1000"),
    )


async def _seed(session: AsyncSession) -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    await repository.upsert_daily_bars([_bar(day) for day in SESSIONS])
    await repository.upsert_positions(
        [
            PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=SESSIONS[0],
                fiz_long=Decimal("125484"),
                fiz_short=None,
                jur_long=None,
                jur_short=None,
            )
        ]
    )
    await repository.upsert_sectors({"EQ_AST_SBER": "Финансы"})
    await session.commit()


def _group(payload: dict[str, object], name: str) -> dict[str, object]:
    rows = payload["groups"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["group"] == name)


async def test_report_has_the_contract_shape(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)

    response = await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})

    assert response.status_code == 200
    payload = response.json()
    assert payload["asof_date"] == ASOF.isoformat()

    quotes = _group(payload, "quotes")
    for required in (
        "group",
        "has_history",
        "window_sessions",
        "sessions_covered",
        "coverage_ratio",
        "period_from",
        "period_till",
        "gaps",
        "rows_total",
        "rows_with_values",
        "value_ratio",
        "looks_collected_but_empty",
    ):
        assert required in quotes


async def test_report_carries_the_catchup_window(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Границы окна догона приходят из ответа, а не выводятся из строк сводки.

    Окна групп различаются, а форме запуска нужно одно — то, по которому
    планируется прогон (FR-013b).
    """
    await _seed(db_session)

    payload = (
        await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})
    ).json()
    window = payload["catchup_window"]

    assert window["sessions"] == len(SESSIONS)
    assert window["date_from"] == SESSIONS[0].isoformat()
    assert window["date_till"] == ASOF.isoformat()


async def test_collected_group_is_not_marked_as_empty(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Вывод «покрыто, но пусто» делает ответ, а не тот, кто его читает.

    Здесь проверяется, что собранная группа признаком не помечается; сам
    признак разбирается в `tests/market_data/test_coverage.py` (FR-013a).
    """
    await _seed(db_session)

    payload = (
        await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})
    ).json()

    assert _group(payload, "quotes")["looks_collected_but_empty"] is False


async def test_reference_row_has_no_window_fields(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """У группы без оси сессий полей окна нет вовсе, а не ноль."""
    await _seed(db_session)

    payload = (
        await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})
    ).json()
    reference = _group(payload, "reference")

    assert reference["has_history"] is False
    assert "window_sessions" not in reference
    assert "coverage_ratio" not in reference


async def test_no_observation_values_are_returned(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Ни цен, ни объёмов, ни позиций."""
    await _seed(db_session)

    response = await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})

    assert "314.22" not in response.text
    assert "125484" not in response.text


async def test_asof_defaults_to_the_last_session(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Без параметра берётся последняя завершённая сессия календаря."""
    await _seed(db_session)

    response = await worker_client.get("/internal/coverage")

    assert response.status_code == 200
    assert response.json()["asof_date"] == ASOF.isoformat()


async def test_empty_calendar_answers_422(
    worker_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Сначала сбор: отсчитывать окна не от чего."""
    response = await worker_client.get("/internal/coverage")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "calendar_empty"
