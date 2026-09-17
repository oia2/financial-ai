"""Бумага перестала торговаться (T038, FR-037, FR-029).

Знаменатель состава — бумаги с котировкой за дату сводки, а не все, что когда-то
были известны. Иначе ушедшая с торгов бумага вечно считалась бы несобранной, и
полнота падала бы сама собой.

Позиции по ней тоже не спрашиваются: обращение, которое заведомо не принесёт
данных, запрещено.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.config import Settings
from financial_ai.market_data import coverage, links
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import positions
from tests.market_data.conftest import FakePositionsClient

from .instruments import RecordedIss, seed_assets

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)
NEXT = dt.date(2026, 9, 17)


async def test_ушедшая_бумага_выпадает_из_знаменателя(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER", "SGZH"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    assert (await coverage.build_report(db_session, Settings(), DAY))["universe"] == {  # type: ignore[arg-type]
        "assets": 2,
        "assets_with_futures": 2,
    }

    # На следующей сессии котировки по SGZH нет: бумага ушла с торгов.
    await seed_assets(repository, NEXT, ["SBER"])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), NEXT)  # type: ignore[arg-type]
    assert report["universe"] == {"assets": 1, "assets_with_futures": 1}

    # Прошлое не переписано: за вчерашнюю сессию бумага в составе была.
    past = await coverage.build_report(db_session, Settings(), DAY)  # type: ignore[arg-type]
    assert past["universe"]["assets"] == 2


async def test_позиции_по_ушедшей_бумаге_не_спрашиваются(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER", "SGZH"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    # Контракта у SGZH больше нет: связь закрывается, и это событие.
    gone = RecordedIss(
        series=[row for row in RecordedIss().series if row["underlying_asset"] != "SGZH"]
    )
    await seed_assets(repository, NEXT, ["SBER", "SGZH"])
    events = await links.sync_links(repository, gone, NEXT)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    closed = [event for event in events if event.kind == links.CLOSED]
    assert [(event.ticker, event.contract_code) for event in closed] == [("SGZH", "SGZH_F")]

    client = FakePositionsClient(contracts={"SBER": "SBRF_F", "SGZH": "SGZH_F"})
    await positions.sync_positions(client, repository, NEXT, client.contract_map)  # type: ignore[arg-type]

    # Спрошен только SBER: связи с SGZH на эту дату нет.
    assert [contract for contract, _ in client.calls] == ["SBRF_F"]
