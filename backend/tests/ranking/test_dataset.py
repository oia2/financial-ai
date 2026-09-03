"""Тесты материализации неизменяемого набора.

Главное свойство — детерминизм дайджеста: он опознаёт вход, и на нём держится
возможность доказать постфактум, на каких данных получено ранжирование.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data.repository import AggregateRow, DailyBar, MarketDataRepository
from financial_ai.ranking.dataset import DatasetError, build_dataset, prune_datasets

pytestmark = pytest.mark.db

SESSIONS = [dt.date(2026, 8, 26), dt.date(2026, 8, 27), dt.date(2026, 8, 28)]
ASOF = SESSIONS[-1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        market_data_dataset_root=str(tmp_path / "datasets"),
        market_data_price_window_sessions=3,
        market_data_global_window_sessions=3,
        market_data_positions_window_sessions=2,
    )


async def _seed(session: AsyncSession, close: str = "314.22") -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    for ticker in ("SBER", "GAZP"):
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, ASOF)
        await repository.upsert_price_series(f"EQ_PRS_{ticker}", f"EQ_AST_{ticker}", ASOF)
    bars = [
        DailyBar(
            asset_id=f"EQ_AST_{ticker}",
            price_series_id=f"EQ_PRS_{ticker}",
            session_date=day,
            open=Decimal("312.4"),
            high=Decimal("315.1"),
            low=Decimal("311.0"),
            close=Decimal(close),
            volume=Decimal("1000"),
        )
        for ticker in ("SBER", "GAZP")
        for day in SESSIONS
    ]
    await repository.upsert_daily_bars(bars)
    await repository.upsert_global_values("IMOEX", {day: Decimal("3200.5") for day in SESSIONS})
    await session.commit()


async def test_dataset_is_built(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)
    assert dataset.asof_date == ASOF
    assert dataset.path.exists()
    assert dataset.digest.startswith("sha256:")


async def test_window_counts_trading_sessions(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)
    assert dataset.sessions == SESSIONS
    assert dataset.windows["price_sessions"] == 3


async def test_manifest_lists_sessions_and_assets(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)
    manifest = json.loads((dataset.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["session_count"] == 3
    assert manifest["asset_count"] == 2
    assert {a["asset_id"] for a in manifest["assets"]} == {"EQ_AST_SBER", "EQ_AST_GAZP"}


async def test_prices_use_compact_layout(db_session: AsyncSession, settings: Settings) -> None:
    """Общая ось сессий, значения позиционно: повторение ключей удваивает объём."""
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)
    prices = json.loads((dataset.path / "prices.json").read_text(encoding="utf-8"))
    assert prices["fields"] == ["open", "high", "low", "close", "volume"]
    assert prices["sessions"] == [d.isoformat() for d in SESSIONS]
    assert isinstance(prices["series"][0]["bars"][0], list)


async def test_prices_are_strings_not_numbers(db_session: AsyncSession, settings: Settings) -> None:
    """Точность обязана дойти без искажений: числа передаются строками."""
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)
    prices = json.loads((dataset.path / "prices.json").read_text(encoding="utf-8"))
    assert all(isinstance(v, str) for v in prices["series"][0]["bars"][0])


# --- дайджест ----------------------------------------------------------------


async def test_same_content_gives_same_digest(db_session: AsyncSession, settings: Settings) -> None:
    """SC-006: одинаковые данные — одинаковый дайджест."""
    await _seed(db_session)
    first = await build_dataset(db_session, settings, ASOF)
    second = await build_dataset(db_session, settings, ASOF)
    assert first.digest == second.digest


async def test_digest_does_not_depend_on_materialisation_time(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Момент сборки в дайджест не входит — иначе он опознавал бы не данные."""
    await _seed(db_session)
    first = await build_dataset(db_session, settings, ASOF)
    manifest = json.loads((first.path / "manifest.json").read_text(encoding="utf-8"))
    assert "materialized_at" in manifest
    second = await build_dataset(db_session, settings, ASOF)
    assert first.digest == second.digest


async def test_changed_value_changes_digest(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session, close="314.22")
    first = await build_dataset(db_session, settings, ASOF)

    await _seed(db_session, close="999.99")
    second = await build_dataset(db_session, settings, ASOF)

    assert first.digest != second.digest


async def test_late_data_creates_new_dataset_not_edits_old(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Неизменяемость: старый набор остаётся как был."""
    await _seed(db_session, close="314.22")
    first = await build_dataset(db_session, settings, ASOF)
    first_prices = (first.path / "prices.json").read_text(encoding="utf-8")

    await _seed(db_session, close="999.99")
    second = await build_dataset(db_session, settings, ASOF)

    assert first.path != second.path
    assert first.path.exists()
    assert (first.path / "prices.json").read_text(encoding="utf-8") == first_prices


# --- отказы ------------------------------------------------------------------


async def test_non_trading_date_is_refused(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session)
    with pytest.raises(DatasetError, match="не является торговой сессией"):
        await build_dataset(db_session, settings, dt.date(2026, 8, 29))


async def test_empty_window_is_refused(db_session: AsyncSession, settings: Settings) -> None:
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(SESSIONS)
    await db_session.commit()
    with pytest.raises(DatasetError, match="нет ни одного наблюдения"):
        await build_dataset(db_session, settings, ASOF)


# --- срок хранения -----------------------------------------------------------


async def test_retention_removes_old_datasets(db_session: AsyncSession, settings: Settings) -> None:
    """Неизменяемость означает накопление: без очистки место закончится."""
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)

    later = dt.datetime.combine(ASOF, dt.time(12, 0), tzinfo=dt.UTC) + dt.timedelta(days=400)
    removed = prune_datasets(settings, now=later)

    assert removed == 1
    assert not dataset.path.exists()


async def test_retention_keeps_recent_datasets(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)

    soon = dt.datetime.combine(ASOF, dt.time(12, 0), tzinfo=dt.UTC) + dt.timedelta(days=1)
    assert prune_datasets(settings, now=soon) == 0
    assert dataset.path.exists()


async def _seed_aggregates_and_sectors(session: AsyncSession) -> None:
    """Дописать к базовому засеву агрегаты и секторы.

    Оба семейства модель читает наравне с котировками: агрегаты попадают в
    `market.equities_agg` и дальше в слой состояния, секторы — в признаки
    отраслевой относительной силы.
    """
    repository = MarketDataRepository(session)
    await repository.upsert_aggregates(
        [
            AggregateRow(
                asset_id=f"EQ_AST_{ticker}",
                price_series_id=f"EQ_PRS_{ticker}",
                session_date=day,
                value=Decimal("1234567.89"),
                num_trades=Decimal("4211"),
                waprice=Decimal("313.987654321"),
            )
            for ticker in ("SBER", "GAZP")
            for day in SESSIONS
        ]
    )
    await repository.upsert_sectors({"EQ_AST_SBER": "financials", "EQ_AST_GAZP": None})
    await session.commit()


async def test_aggregates_reach_the_dataset(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session)
    await _seed_aggregates_and_sectors(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)

    payload = json.loads((dataset.path / "aggregates.json").read_text(encoding="utf-8"))
    assert payload["fields"] == ["value", "num_trades", "waprice"]
    assert [s["asset_id"] for s in payload["series"]] == ["EQ_AST_GAZP", "EQ_AST_SBER"]
    assert dataset.windows["aggregate_sessions"] == 3


async def test_aggregate_precision_survives(db_session: AsyncSession, settings: Settings) -> None:
    """Точность агрегатов доходит строкой: float исказил бы `waprice`."""
    await _seed(db_session)
    await _seed_aggregates_and_sectors(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)

    payload = json.loads((dataset.path / "aggregates.json").read_text(encoding="utf-8"))
    rows = payload["series"][0]["rows"]
    assert rows[-1] == ["1234567.890000000", "4211.000000000", "313.987654321"]


async def test_sectors_reach_the_dataset(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session)
    await _seed_aggregates_and_sectors(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)

    payload = json.loads((dataset.path / "sectors.json").read_text(encoding="utf-8"))
    assert payload["sectors"] == {"EQ_AST_GAZP": None, "EQ_AST_SBER": "financials"}


async def test_unknown_sector_is_none_not_empty_string(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Неизвестная отрасль остаётся пропуском: пустая строка стала бы отраслью."""
    await _seed(db_session)
    await _seed_aggregates_and_sectors(db_session)
    dataset = await build_dataset(db_session, settings, ASOF)

    payload = json.loads((dataset.path / "sectors.json").read_text(encoding="utf-8"))
    assert payload["sectors"]["EQ_AST_GAZP"] is None


async def test_digest_changes_when_aggregates_change(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Иначе набор с другими агрегатами выдал бы себя за прежний."""
    await _seed(db_session)
    without = (await build_dataset(db_session, settings, ASOF)).digest

    await _seed_aggregates_and_sectors(db_session)
    with_aggregates = (await build_dataset(db_session, settings, ASOF)).digest

    assert without != with_aggregates
