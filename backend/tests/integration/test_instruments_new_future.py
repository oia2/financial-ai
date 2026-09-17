"""У бумаги впервые появился фьючерс (T035, FR-015, FR-029).

Связь открывается с той даты, на которую она подтверждена, и ни днём раньше.
Знать «фьючерс был и вчера» неоткуда: список серий отвечает про сегодня, и
задним числом открытая связь заставила бы ежедневный сбор спрашивать позиции за
периоды, когда инструмента не существовало.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository

from .instruments import RecordedIss, recorded_series, seed_assets, without

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)
NEXT = dt.date(2026, 9, 17)


async def test_связь_открывается_с_подтверждённой_даты(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER", "SGZH"])

    # Вчера у SGZH контрактов не было вовсе.
    yesterday = RecordedIss(series=without(recorded_series(), underlying_asset="SGZH"))
    await links.sync_links(repository, yesterday, DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    assert await repository.link_history("EQ_AST_SGZH") == []

    # Сегодня контракт появился.
    await seed_assets(repository, NEXT, ["SBER", "SGZH"])
    events = await links.sync_links(repository, RecordedIss(), NEXT)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    history = await repository.link_history("EQ_AST_SGZH")
    assert [(row.contract_code, row.valid_from, row.valid_till) for row in history] == [
        ("SGZH_F", NEXT, None)
    ]

    appeared = [event for event in events if event.ticker == "SGZH"]
    assert [event.kind for event in appeared] == [links.OPENED]


async def test_вчерашняя_дата_связью_не_накрыта(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, NEXT, ["SGZH"])
    await links.sync_links(repository, RecordedIss(), NEXT)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    # Ежедневный сбор спрашивает позиции по действующей связи. На вчера её нет,
    # значит и обращения за вчера не будет: инструмента тогда не было.
    assert await repository.active_links_on(DAY) == {}
    assert await repository.active_links_on(NEXT) == {"EQ_AST_SGZH": "SGZH_F"}
