"""Фикстуры тестов жизненного цикла Daily ML.

Сеть не задействуется: звено ранжирования подделывается транспортом httpx.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import groups
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
    dt.date(2026, 9, 1),
]
ASOF = SESSIONS[-1]

LINK_URL = "http://daily-ml-emulator:8000"
HEALTH_URL = f"{LINK_URL}/health"
RANKINGS_URL = f"{LINK_URL}/rankings"

MODEL_ID = "daily-ml-emulator"
MODEL_VERSION = "emulator-v1"


@pytest.fixture
def settings(tmp_path: object) -> Settings:
    """Окна сужены до засеянных сессий: иначе в окно попадёт пустота.

    Обязательными объявлены только котировки: остальные группы засеивать ради
    проверки жизненного цикла незачем, а перечень на то и конфигурация.
    """
    return Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_global_window_sessions=len(SESSIONS),
        market_data_positions_window_sessions=len(SESSIONS),
        market_data_catchup_window_sessions=len(SESSIONS),
        market_data_dataset_root=str(tmp_path),
        daily_ml_required_data_groups=["quotes"],
    )


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("312.4"),
        high=Decimal("315.1"),
        low=Decimal("311.0"),
        close=Decimal("314.22"),
        volume=Decimal("1000"),
    )


async def seed(
    session: AsyncSession,
    collected: list[dt.date] | None = None,
) -> MarketDataRepository:
    """Календарь, актив и собранные сессии котировок.

    Успешный прогон сбора записывается отдельно от самих баров: готовность
    смотрит именно на прогоны, потому что «строк нет» и «источник не отработал»
    это разные факты.
    """
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)

    days = SESSIONS if collected is None else collected
    if days:
        await repository.upsert_daily_bars([_bar(day) for day in days])

    quotes = next(g for g in groups.GROUPS if g.group_id.value == "quotes")
    now = dt.datetime.now(dt.UTC)
    for day in days:
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
    return repository


def ranking_payload(digest: str, asof: dt.date = ASOF) -> dict[str, object]:
    """Ответ звена ранжирования по контракту фичи 002."""
    return {
        "asof_date": asof.isoformat(),
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "input_digest": digest,
        "generated_at": "2026-09-01T17:30:05Z",
        "emulated": True,
        "emulation_notice": "значения вымышлены",
        "included_asset_count": 1,
        "excluded": [],
        "items": [
            {
                "rank": 1,
                "asset_id": "EQ_AST_SBER",
                "price_series_id": "EQ_PRS_SBER",
                "score": "0.500000000",
            }
        ],
    }


@pytest.fixture
def ranking_link() -> Iterator[respx.MockRouter]:
    """Подделка звена ранжирования: здоровье и ранжирование.

    Перехват через respx, а не подменой клиента: клиент создаётся внутри
    исполнителя, и подменять его пришлось бы протаскиванием параметра через
    всю цепочку — ради теста менять устройство продукта нельзя.
    """
    import json

    import httpx

    def rank(request: httpx.Request) -> httpx.Response:
        # Дайджест возвращается тот же, что пришёл: клиент сверяет его и
        # отвергает ответ, относящийся к другому набору.
        payload = json.loads(request.content)
        return httpx.Response(200, json=ranking_payload(payload["dataset"]["digest"]))

    with respx.mock(assert_all_called=False) as router:
        router.get(HEALTH_URL).respond(
            json={"status": "ok", "model_id": MODEL_ID, "model_version": MODEL_VERSION}
        )
        router.post(RANKINGS_URL).mock(side_effect=rank)
        yield router
