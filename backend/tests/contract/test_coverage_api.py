"""Состав бумаг в сводке — contracts/coverage-api.md (spec 008).

Неполнота группы позиций должна объясняться числами, а не догадкой: фьючерс
есть не у каждой бумаги, и её отсутствие — не пропуск. Числа считаются по
хранилищу: открытие раздела не должно стоить обращений к бирже.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from financial_ai.config import Settings
from financial_ai.market_data import coverage
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

ASOF = dt.date(2026, 9, 16)


async def seed(session: object, tickers: list[str]) -> MarketDataRepository:
    repository = MarketDataRepository(session)  # type: ignore[arg-type]
    await repository.add_trading_sessions([ASOF])

    bars = []
    for ticker in tickers:
        asset_id = f"EQ_AST_{ticker}"
        series_id = f"EQ_PRS_{ticker}"
        await repository.upsert_asset(asset_id, ticker, ASOF)
        await repository.upsert_price_series(series_id, asset_id, ASOF)
        bars.append(
            DailyBar(
                asset_id=asset_id,
                price_series_id=series_id,
                session_date=ASOF,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        )
    await repository.upsert_daily_bars(bars)
    return repository


async def test_знаменатель_это_бумаги_с_котировкой_за_дату(db_session: object) -> None:
    repository = await seed(db_session, ["SBER", "GAZP", "LKOH"])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]

    assert report["universe"] == {"assets": 3, "assets_with_futures": 0}
    assert repository is not None


async def test_бумаги_с_фьючерсом_считаются_по_действующей_связи(db_session: object) -> None:
    repository = await seed(db_session, ["SBER", "GAZP", "SGZH"])

    # Связь действует с более ранней даты и не закрыта — значит действует и на
    # дату сводки.
    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_F",
        valid_from=dt.date(2026, 9, 1),
        chosen_by="underlying_and_emitter",
    )
    await repository.open_link(
        asset_id="EQ_AST_GAZP",
        contract_code="GAZR_F",
        valid_from=dt.date(2026, 9, 1),
        chosen_by="underlying_and_emitter",
    )
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]

    # У SGZH фьючерса нет — и это не пропуск, а отсутствие инструмента.
    assert report["universe"] == {"assets": 3, "assets_with_futures": 2}


async def test_закрытая_связь_в_состав_не_попадает(db_session: object) -> None:
    repository = await seed(db_session, ["SBER"])
    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_F",
        valid_from=dt.date(2026, 8, 1),
        chosen_by="underlying_only",
    )
    # Смена контракта закрывает прежний интервал следующим днём после 2026-09-20,
    # то есть на дату сводки действует первый.
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    assert report["universe"]["assets_with_futures"] == 1

    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_MINI",
        valid_from=dt.date(2026, 9, 17),
        chosen_by="underlying_only",
    )
    await db_session.commit()  # type: ignore[attr-defined]

    # Прежняя связь закрыта 16.09, новая начинается 17.09: на 16.09 действует
    # ещё старая, и состав не меняется.
    again = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    assert again["universe"]["assets_with_futures"] == 1


async def test_у_группы_есть_исход_каждого_источника(db_session: object) -> None:
    await seed(db_session, ["SBER"])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    groups = {row["group"]: row for row in report["groups"]}

    # У «глобальных рядов» четыре источника: неполнота должна называть, какой
    # именно ряд не собрался, а не оставаться числом.
    assert len(groups["global"]["sources"]) == 4

    # У справочника оси сессий нет, но источники есть: пустой список читался бы
    # как «источников ноль».
    assert {source["source_id"] for source in groups["reference"]["sources"]} == {
        "equity_sectors",
        "equity_lot_sizes",
    }
    assert {source["source_id"] for source in groups["global"]["sources"]} == {
        "global_series",
        "cbr",
        "brent",
        "index_constituents",
    }
