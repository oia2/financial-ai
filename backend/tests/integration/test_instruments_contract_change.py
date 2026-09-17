"""Фьючерс сменил семейство контрактов (T036, FR-016, FR-029).

Выбор идёт по открытому интересу, и он может переключиться между семействами —
у SBER их два: классическое `SBRF` и вечное `SBERF`. Прежде такое переключение
проходило бесследно, и ряд позиций склеивался из двух разных инструментов.

Проверяется, что прежний интервал закрывается, новый открывается, и смена
называется человеку событием.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository

from .instruments import RecordedIss, recorded_open_interest, seed_assets

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)
NEXT = dt.date(2026, 9, 17)


async def test_смена_семейства_закрывает_прежний_интервал(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER"])

    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    first = await repository.active_links_on(DAY)
    assert first == {"EQ_AST_SBER": "SBRF_F"}

    # Интерес перетёк в вечный контракт.
    moved = recorded_open_interest()
    moved["SBERF"] = max(moved.values()) + 1
    await seed_assets(repository, NEXT, ["SBER"])
    events = await links.sync_links(  # type: ignore[arg-type]
        repository, RecordedIss(open_interest=moved), NEXT
    )
    await db_session.commit()  # type: ignore[attr-defined]

    history = await repository.link_history("EQ_AST_SBER")
    assert [(row.contract_code, row.valid_from, row.valid_till) for row in history] == [
        ("SBRF_F", DAY, DAY),
        ("SBERF_F", NEXT, None),
    ]

    # Прошлое не переписано: на вчерашнюю дату действует вчерашний контракт.
    assert await repository.active_links_on(DAY) == {"EQ_AST_SBER": "SBRF_F"}
    assert await repository.active_links_on(NEXT) == {"EQ_AST_SBER": "SBERF_F"}

    changed = [event for event in events if event.kind == links.CHANGED]
    assert [(event.ticker, event.contract_code) for event in changed] == [("SBER", "SBERF_F")]


async def test_выбор_записан_с_основанием(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER"])

    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    (row,) = await repository.link_history("EQ_AST_SBER")
    # Эмитент контракта `SRH7` и бумаги `SBER` — 484: выбор подтверждён, и это
    # записано. Через год «почему у этой бумаги такой контракт» — вопрос к
    # данным, а не к памяти.
    assert row.chosen_by == links.BY_UNDERLYING_AND_EMITTER
    assert row.open_interest is not None and row.open_interest > 0
