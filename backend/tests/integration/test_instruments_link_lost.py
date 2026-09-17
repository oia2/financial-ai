"""Бумага перестала сопоставляться (T040, FR-020a, FR-029).

Разница между «у бумаги нет фьючерса» и «мы потеряли соответствие» — это
разница между нормой и дефектом. Первое ничего не требует, второе требует
вмешательства, и именно поэтому промах сопоставления обязан записываться
неуспехом источника с причиной, а не тихо уменьшать число собранных бумаг.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository, PositionRow
from financial_ai.market_data.sources import positions
from tests.market_data.conftest import FakePositionsClient

from .instruments import (
    RecordedIss,
    recorded_emitters,
    recorded_open_interest,
    recorded_series,
    seed_assets,
)

pytestmark = pytest.mark.db

DAY = dt.date(2026, 9, 16)
NEXT = dt.date(2026, 9, 17)


async def _with_positions(repository: MarketDataRepository) -> None:
    await repository.upsert_positions(
        [
            PositionRow(
                asset_id="EQ_AST_SGZH",
                session_date=DAY,
                contract_code="SGZH_F",
                fiz_long=Decimal("10"),
                fiz_short=Decimal("5"),
                jur_long=Decimal("7"),
                jur_short=Decimal("3"),
            )
        ]
    )


async def test_потеря_связи_у_бумаги_с_историей_это_неуспех(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER", "SGZH"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await _with_positions(repository)
    await db_session.commit()  # type: ignore[attr-defined]

    # Контракт исчез из списка серий: связь закрывается.
    gone = RecordedIss(series=[r for r in recorded_series() if r["underlying_asset"] != "SGZH"])
    await seed_assets(repository, NEXT, ["SBER", "SGZH"])
    await links.sync_links(repository, gone, NEXT)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    client = FakePositionsClient(contracts={"SBER": "SBRF_F"})
    with pytest.raises(positions.EmptyPositionsError) as failure:
        await positions.sync_positions(client, repository, NEXT, client.contract_map)  # type: ignore[arg-type]

    # Причина названа бумагой, а не числом: без имени человеку нечего проверять.
    assert "SGZH" in str(failure.value)
    assert "потеряли связь" in str(failure.value)


async def test_бумага_без_истории_позиций_неуспехом_не_является(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER", "MOEX"])
    # У MOEX контракта нет вовсе, истории позиций тоже нет: это норма.
    no_moex = RecordedIss(series=[r for r in recorded_series() if r["underlying_asset"] != "MOEX"])
    await links.sync_links(repository, no_moex, DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    client = FakePositionsClient(contracts={"SBER": "SBRF_F"})
    written = await positions.sync_positions(client, repository, DAY, client.contract_map)  # type: ignore[arg-type]

    assert written == 1
    assert [contract for contract, _ in client.calls] == ["SBRF_F"]


async def test_чужой_эмитент_не_выдаётся_за_отсутствие_фьючерса(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, DAY, ["SBER"])
    await links.sync_links(repository, RecordedIss(), DAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    # Биржа предлагает сменить контракт, но эмитент нового не совпадает с
    # эмитентом бумаги: сопоставление промахнулось.
    swapped = [
        {**row, "asset_code": "SBRFX" if row["asset_code"] == "SBRF" else row["asset_code"]}
        for row in recorded_series()
    ]
    interest = recorded_open_interest()
    interest["SBRFX"] = interest["SBRF"]
    emitters = {**recorded_emitters(), "SRH7": "711"}

    await seed_assets(repository, NEXT, ["SBER"])
    with pytest.raises(links.LinkMismatchError) as failure:
        await links.sync_links(  # type: ignore[arg-type]
            repository,
            RecordedIss(series=swapped, open_interest=interest, emitters=emitters),
            NEXT,
        )

    assert "эмитент" in str(failure.value)
