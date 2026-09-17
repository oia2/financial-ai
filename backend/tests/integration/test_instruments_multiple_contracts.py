"""У бумаги несколько контрактов (T039, FR-036, FR-029).

У SBER два семейства: классическое `SBRF` и вечное `SBERF`; у GAZP — `GAZR` и
`GAZPF`. Выбор должен быть однозначным, повторяемым и объяснённым: иначе он
молча менялся бы от прогона к прогону вместе с порядком строк в ответе биржи, а
ряд позиций склеивался бы из двух инструментов.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository

from .instruments import RecordedIss, recorded_open_interest, recorded_series, seed_assets

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)


async def test_выбор_однозначен_и_объяснён(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER", "GAZP"])

    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    # Позиции живут там, где торгуют: выбрано семейство с бóльшим открытым
    # интересом, а не первое по алфавиту или по порядку ответа.
    assert await repository.active_links_on(DAY) == {
        "EQ_AST_SBER": "SBRF_F",
        "EQ_AST_GAZP": "GAZR_F",
    }

    for asset_id in ("EQ_AST_SBER", "EQ_AST_GAZP"):
        (row,) = await repository.link_history(asset_id)
        assert row.chosen_by in {links.BY_UNDERLYING, links.BY_UNDERLYING_AND_EMITTER}
        assert row.open_interest is not None


async def test_порядок_ответа_биржи_на_выбор_не_влияет(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER"])

    shuffled = recorded_series()
    random.Random(20260917).shuffle(shuffled)

    first = await links.build_candidates(RecordedIss())  # type: ignore[arg-type]
    second = await links.build_candidates(RecordedIss(series=shuffled))  # type: ignore[arg-type]

    assert first == second


async def test_при_равном_интересе_выбор_не_случаен(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER"])

    # Ровно равный интерес — худший случай: без второго ключа сортировки выбор
    # зависел бы от порядка обхода множества и менялся бы от прогона к прогону.
    # Второй ключ — имя семейства, поэтому побеждает первое по алфавиту.
    tied = dict.fromkeys(recorded_open_interest(), 1000)

    chosen = {
        (await links.build_candidates(RecordedIss(open_interest=tied)))["SBER"].contract_code  # type: ignore[arg-type]
        for _ in range(5)
    }
    assert chosen == {"SBERF_F"}
