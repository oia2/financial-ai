"""Сводка отвечает по хранилищу, а не по бирже (SC-006).

Раздел открывают чаще, чем идёт сбор. Если бы сводка ходила к источнику, каждое
обновление страницы стоило бы обращений к ISS, а недоступность биржи выглядела бы
как отсутствие данных — хотя данные собраны и лежат в базе.

Испытание держит это правилом: любой исходящий запрос во время ответа сводки
превращается в отказ `respx`, то есть в упавший тест.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx

from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

ASOF = dt.date(2026, 9, 16)


async def _seed(session: object) -> None:
    repository = MarketDataRepository(session)  # type: ignore[arg-type]
    await repository.add_trading_sessions([ASOF])
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=ASOF,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        ]
    )
    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_F",
        valid_from=dt.date(2026, 9, 1),
        chosen_by="underlying_and_emitter",
    )
    await session.commit()  # type: ignore[attr-defined]


async def test_сводка_отвечает_не_обращаясь_к_бирже(
    db_session: object, worker_client: httpx.AsyncClient
) -> None:
    await _seed(db_session)

    # Ни один маршрут не зарегистрирован: respx отклонит любой исходящий запрос.
    with respx.mock(assert_all_called=False) as mock:
        response = await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})

        outgoing = [call for call in mock.calls if "testserver" not in str(call.request.url)]

    assert response.status_code == 200
    assert outgoing == []


async def test_состав_бумаг_в_ответе_совпадает_с_хранилищем(
    db_session: object, worker_client: httpx.AsyncClient
) -> None:
    await _seed(db_session)

    response = await worker_client.get("/internal/coverage", params={"asof": ASOF.isoformat()})

    assert response.json()["universe"] == {
        "assets": 1,
        "assets_with_futures": 1,
        "asof_date": ASOF.isoformat(),
    }
