"""Фикстуры плана портфеля.

Стенд собирается минимальным: три актива с ценами и лотами, счёт с деньгами и
двумя позициями, один успешный прогон ранжирования. Ничего лишнего — проверять
надо арифметику, а не умение засеивать базу.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml.repository import DailyMlRepository, RunInput
from financial_ai.db import repository as portfolio_repository
from financial_ai.domain.models import AccountSnapshot, BrokerAccount, PositionState
from financial_ai.market_data import groups
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

SESSIONS = [dt.date(2026, 8, 31), dt.date(2026, 9, 1)]
ASOF = SESSIONS[-1]

# Активов ровно столько, сколько требует самое маленькое правило: двадцать.
# Цены и лоты подобраны так, чтобы округление до лота считалось на бумаге. При
# капитале 100 000 ₽ и равных долях по 5 000 ₽:
#
#   SBER — лот 10 по 300 ₽ (3 000 ₽ за лот): один лот, 2 000 ₽ остатка;
#   LKOH — лот 1 по 2 500 ₽: два лота ровно в долю;
#   GAZP — лот 10 по 130 ₽ (1 300 ₽ за лот): три лота, 1 100 ₽ остатка;
#   остальные — лот 1 по 100 ₽: пятьдесят лотов ровно в долю.
#
# Активов двадцать один: двадцать входят в выборку правила, последний остаётся
# вне её — на нём проверяется закрытие позиции вне целевого состава.
ASSETS: dict[str, tuple[str, int, Decimal]] = {
    "EQ_AST_SBER": ("SBER", 10, Decimal("300.00")),
    "EQ_AST_LKOH": ("LKOH", 1, Decimal("2500.00")),
    "EQ_AST_GAZP": ("GAZP", 10, Decimal("130.00")),
    **{
        f"EQ_AST_FILL{index:02d}": (f"FILL{index:02d}", 1, Decimal("100.00"))
        for index in range(1, 19)
    },
}

ALL_ASSETS = tuple(ASSETS)

# Двадцать из двадцати одного: правило `equal_top20` берёт ровно столько, и
# последний актив остаётся вне выборки — на нём проверяется закрытие позиции.
SELECTION = ALL_ASSETS[:20]


@pytest.fixture
def settings(tmp_path: object) -> Settings:
    return Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_global_window_sessions=len(SESSIONS),
        market_data_positions_window_sessions=len(SESSIONS),
        market_data_dataset_root=str(tmp_path),
        daily_ml_required_data_groups=["quotes"],
    )


async def seed_market(session: AsyncSession, lots: dict[str, int | None] | None = None) -> None:
    """Календарь, справочник активов с лотами и закрытия последней сессии."""
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)

    bars = []
    sizes: dict[str, int] = {}
    for asset_id, (ticker, lot, price) in ASSETS.items():
        series_id = f"EQ_PRS_{ticker}"
        await repository.upsert_asset(asset_id, ticker, ASOF)
        await repository.upsert_price_series(series_id, asset_id, ASOF)

        override = (lots or {}).get(asset_id, lot)
        if override is not None:
            sizes[asset_id] = override

        for day in SESSIONS:
            bars.append(
                DailyBar(
                    asset_id=asset_id,
                    price_series_id=series_id,
                    session_date=day,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=Decimal("1000"),
                )
            )

    await repository.upsert_daily_bars(bars)
    await repository.update_lot_sizes(sizes)

    quotes = next(g for g in groups.GROUPS if g.group_id.value == "quotes")
    now = dt.datetime.now(dt.UTC)
    for day in SESSIONS:
        for source_id in quotes.source_ids:
            await repository.record_run(
                run_id=f"seed-{day.isoformat()}",
                source_id=source_id,
                status="ok",
                started_at=now,
                finished_at=now,
                session_date=day,
                rows_written=1,
            )

    await session.commit()


def position(
    ticker: str,
    quantity: Decimal,
    price: Decimal,
    asset_type: str = "share",
    accrued: Decimal = Decimal(0),
) -> PositionState:
    value = quantity * (price + accrued)
    return PositionState(
        instrument_uid=f"uid-{ticker}",
        ticker=ticker,
        name=ticker,
        asset_type=asset_type,
        currency="RUB",
        quantity=quantity,
        average_price=price,
        current_price=price,
        accrued_interest=accrued,
        value=value,
        unrealized_pnl=Decimal(0),
        cost_basis=value,
    )


async def seed_account(
    session: AsyncSession,
    cash: Decimal = Decimal("100000.00"),
    positions: tuple[PositionState, ...] = (),
    captured_at: dt.datetime | None = None,
) -> None:
    """Состояние счёта. Свежий снимок — иначе расчёт откажется считать."""
    snapshot = AccountSnapshot(
        captured_at=captured_at or dt.datetime.now(dt.UTC),
        total_value=cash + sum((p.value for p in positions), Decimal(0)),
        cash=cash,
        positions_cost_basis=Decimal(0),
        unrealized_pnl=Decimal(0),
        positions_count=len(positions),
        positions=positions,
    )
    account = BrokerAccount(
        broker_account_id="2000000001",
        masked_id="•••0001",
        display_name="Основной",
        currency="RUB",
    )
    await portfolio_repository.save_snapshot(session, account, snapshot)
    await session.commit()


async def seed_ranking(
    session: AsyncSession,
    settings: Settings,
    order: tuple[str, ...] = SELECTION,
    digest: str | None = None,
) -> int:
    """Успешный прогон с заданным порядком активов.

    Дайджест по умолчанию берётся у настоящего набора: расчёт отказывается
    считать по устаревшему входу, и выдуманный дайджест выглядел бы как
    устаревание, а не как ошибка стенда.
    """
    from financial_ai.ranking import dataset as dataset_module

    if digest is None:
        digest = (await dataset_module.build_dataset(session, settings, ASOF)).digest

    repository = DailyMlRepository(session)
    run, _ = await repository.enqueue(
        RunInput(ASOF, digest, "daily-ml-emulator", "emulator-v1"),
        "file:///datasets/нет-такого",
        window=(SESSIONS[0], ASOF),
        input_complete=True,
    )
    now = dt.datetime.now(dt.UTC)
    await repository.mark_running(run.id, now)
    await repository.mark_success(
        run.id,
        now,
        [
            (
                rank,
                asset_id,
                f"EQ_PRS_{ASSETS[asset_id][0]}" if asset_id in ASSETS else "EQ_PRS_UNKNOWN",
                Decimal("0.9") - Decimal(rank) / Decimal(100),
            )
            for rank, asset_id in enumerate(order, start=1)
        ],
        emulated=True,
    )
    await session.commit()
    return run.id
