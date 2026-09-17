"""Бумага переименована (T037, FR-038, FR-029).

Тикер — имя бумаги на период, а не сама бумага. Раньше ключом был он, и
переименование выглядело бы появлением новой бумаги с пустой историей рядом с
прежней, «ушедшей с торгов». ISIN не меняется, и по нему переименование
опознаётся как переименование.

Проверяется, что сущность остаётся прежней: наблюдения под новым именем ложатся
в прежний ряд, связь с контрактом не рвётся, а само переименование названо.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import rows_to_bars

from .instruments import RecordedIss, recorded_isins, recorded_series, seed_assets

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)
NEXT = dt.date(2026, 9, 17)

# Тот же ISIN под новым именем: так выглядит переименование на доске.
RENAMED_ISINS = {
    ticker if ticker != "SGZH" else "SGZ": isin for ticker, isin in recorded_isins().items()
}
RENAMED_SERIES = [
    {
        **row,
        "underlying_asset": (
            "SGZ" if row["underlying_asset"] == "SGZH" else row["underlying_asset"]
        ),
    }
    for row in recorded_series()
]


async def test_переименование_не_заводит_вторую_бумагу(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SGZH"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    events = await links.sync_links(  # type: ignore[arg-type]
        repository,
        RecordedIss(isins=RENAMED_ISINS, series=RENAMED_SERIES),
        NEXT,
    )
    await db_session.commit()  # type: ignore[attr-defined]

    renamed = [event for event in events if event.kind == links.RENAMED]
    assert [(event.ticker, event.asset_id) for event in renamed] == [("SGZ", "EQ_AST_SGZH")]

    # Новое имя ведёт к прежней сущности, и только к ней.
    aliases = await repository.aliases_on(NEXT)
    assert aliases["SGZ"] == "EQ_AST_SGZH"
    assert "SGZH" not in aliases


async def test_наблюдения_под_новым_именем_ложатся_в_прежний_ряд(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SGZH"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await links.sync_links(  # type: ignore[arg-type]
        repository,
        RecordedIss(isins=RENAMED_ISINS, series=RENAMED_SERIES),
        NEXT,
    )
    await db_session.commit()  # type: ignore[attr-defined]

    aliases = await repository.aliases_on(NEXT)
    bars = rows_to_bars([{"SECID": "SGZ", "CLOSE": "12.5"}], NEXT, aliases)

    # Ряд прежний: история не рвётся на дате переименования.
    assert [(bar.asset_id, bar.price_series_id) for bar in bars] == [("EQ_AST_SGZH", "EQ_PRS_SGZH")]


async def test_связь_с_контрактом_переживает_переименование(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SGZH"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    await links.sync_links(  # type: ignore[arg-type]
        repository,
        RecordedIss(isins=RENAMED_ISINS, series=RENAMED_SERIES),
        NEXT,
    )
    await db_session.commit()  # type: ignore[attr-defined]

    # Одна связь, а не две: контракт остался тем же, сменилось только имя.
    history = await repository.link_history("EQ_AST_SGZH")
    assert [(row.contract_code, row.valid_from, row.valid_till) for row in history] == [
        ("SGZH_F", DAY, None)
    ]
