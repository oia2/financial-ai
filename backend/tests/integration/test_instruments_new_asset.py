"""Появилась новая бумага (T034, FR-029).

Состав инструментов меняется сам по себе: бумага выходит на торги, и сбор про
неё ещё ничего не знает. Проверяется, что прогон не падает, бумага попадает в
состав и в знаменатель сводки, а её появление не выглядит пропуском сбора.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.config import Settings
from financial_ai.market_data import coverage, links
from financial_ai.market_data.repository import MarketDataRepository

from .instruments import RecordedIss, recorded_series, seed_assets

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)
NEXT = dt.date(2026, 9, 17)


async def test_новая_бумага_входит_в_состав_и_получает_связь(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    iss = RecordedIss()

    await seed_assets(repository, DAY, ["SBER", "GAZP"])
    await links.sync_links(repository, iss, DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    before = await coverage.build_report(db_session, Settings(), DAY)  # type: ignore[arg-type]
    assert before["universe"] == {
        "assets": 2,
        "assets_with_futures": 2,
        "asof_date": DAY.isoformat(),
    }

    # На следующей сессии на доске появляется третья бумага.
    await seed_assets(repository, NEXT, ["SBER", "GAZP", "LKOH"])
    events = await links.sync_links(repository, iss, NEXT)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    after = await coverage.build_report(db_session, Settings(), NEXT)  # type: ignore[arg-type]
    assert after["universe"] == {
        "assets": 3,
        "assets_with_futures": 3,
        "asof_date": NEXT.isoformat(),
    }

    # Появление названо событием: без него рост знаменателя выглядел бы
    # ухудшением полноты.
    opened = [event for event in events if event.kind == links.OPENED]
    assert [event.ticker for event in opened] == ["LKOH"]


async def test_бумага_без_фьючерса_не_считается_пропуском(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    # MOEX на доске есть, а в списке серий её нет вовсе.
    iss = RecordedIss(
        series=[row for row in recorded_series() if row["underlying_asset"] != "MOEX"]
    )

    await seed_assets(repository, DAY, ["SBER", "MOEX"])
    await links.sync_links(repository, iss, DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), DAY)  # type: ignore[arg-type]

    # Две бумаги, фьючерс у одной. Отсутствие инструмента — не пропуск сбора.
    assert report["universe"] == {
        "assets": 2,
        "assets_with_futures": 1,
        "asof_date": DAY.isoformat(),
    }
