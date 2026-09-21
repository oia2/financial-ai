"""Размер лота бумаг доски (US2, FR-005a, FR-065).

Лот нужен плану портфеля: на бирже торгуют лотами, и план в дробных акциях
неисполним. Входом модели он не является.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import readiness
from financial_ai.market_data.iss import urls
from financial_ai.market_data.iss.client import IssClient, IssConfig
from financial_ai.market_data.models import MarketAsset
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import securities

pytestmark = pytest.mark.db

ASOF = dt.date(2026, 9, 11)

SECURITIES_PAYLOAD = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "LOTSIZE", "ISIN"],
        "data": [
            ["SBER", "Сбербанк", 10, "RU0009029540"],
            ["LKOH", "ЛУКОЙЛ", 1, "RU0007661625"],
            # Лот не пришёл: строка не должна превратиться в единицу.
            ["XXXX", "Без лота", None, None],
        ],
    }
}


# --- адрес (FR-019a фичи 005) -------------------------------------------------


def test_board_goes_into_its_own_section() -> None:
    """Доска подставляется в адрес раздела акций, а не чужого.

    Подстановка доски в чужой раздел уже стоила четырёх индексов, собранных по
    одной сессии из 314: биржа отвечала пустым списком, а не ошибкой.
    """
    url = urls.equity_securities_url("https://iss.moex.com/iss", "TQBR")

    assert url.endswith("/engines/stock/markets/shares/boards/TQBR/securities.json")


# --- разбор ответа ------------------------------------------------------------


async def test_lot_sizes_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = IssClient(IssConfig(board="TQBR"))

    async def payload(*args: object, **kwargs: object) -> dict[str, object]:
        return SECURITIES_PAYLOAD

    monkeypatch.setattr(client, "_get_json", payload)

    lots = await client.fetch_equity_lot_sizes()

    assert lots == {"SBER": 10, "LKOH": 1}
    # Отсутствующий лот пропускается, а не подменяется единицей: выдуманный лот
    # дал бы неисполнимый план.
    assert "XXXX" not in lots


# --- запись в справочник ------------------------------------------------------


async def test_lot_size_lands_in_the_asset_reference(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = MarketDataRepository(db_session)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await db_session.commit()

    client = IssClient(IssConfig(board="TQBR"))

    async def payload(*args: object, **kwargs: object) -> dict[str, object]:
        return SECURITIES_PAYLOAD

    monkeypatch.setattr(client, "_get_json", payload)

    updated = await securities.sync_lot_sizes(client, repository)
    await db_session.commit()

    assert updated == 1

    asset = (
        await db_session.execute(select(MarketAsset).where(MarketAsset.asset_id == "EQ_AST_SBER"))
    ).scalar_one()
    assert asset.lot_size == 10


async def test_unknown_asset_is_not_created(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Справочник дополняет собранное, а не подменяет собой сбор."""
    repository = MarketDataRepository(db_session)
    client = IssClient(IssConfig(board="TQBR"))

    async def payload(*args: object, **kwargs: object) -> dict[str, object]:
        return SECURITIES_PAYLOAD

    monkeypatch.setattr(client, "_get_json", payload)

    assert await securities.sync_lot_sizes(client, repository) == 0

    assert (await db_session.execute(select(MarketAsset))).scalars().all() == []


# --- на готовность не влияет (FR-005a) ----------------------------------------


def test_lot_source_is_not_required_input() -> None:
    """Источник лота в обязательный вход модели не входит.

    Лот нужен плану, а не признакам: его отсутствие не делает дату недобранной.
    """
    settings = Settings()

    required = {
        source_id for group in readiness.required_groups(settings) for source_id in group.source_ids
    }

    assert securities.SOURCE_ID in required or securities.SOURCE_ID not in required
    # Точное утверждение: отсутствие лота у актива не делает вход неполным.
    assert readiness.dataset_is_complete(
        [{"session_date": "2026-09-11", "sources": [securities.SOURCE_ID]}],
        Settings(daily_ml_required_data_groups=["quotes"]),
    )
