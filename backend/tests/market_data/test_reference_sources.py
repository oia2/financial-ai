"""Тесты сбора справочников из раздела аналитики индексов.

Оба справочника были сломаны одинаково: их спрашивали у истории торгов, где
нужных колонок нет. Биржа отвечала `200`, строки писались, значения оставались
пустыми — 62 584 строки весов при пяти непустых и 506 строк отраслей, где
отрасль не заполнена ни у одной.

Поэтому здесь проверяется не только разбор, но и **непустота результата**: тест
на подделке с правильными колонками прошёл бы и в том, и в другом случае.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import reference

pytestmark = pytest.mark.db

SESSION = dt.date(2026, 8, 28)

# Названия — с живого ответа `analytics.json`.
TITLES = {
    "MOEXOG": "Индекс нефти и газа",
    "MOEXMM": "Индекс металлов и добычи",
    "MOEXFN": "Индекс финансов",
}


class FakeIss:
    """Подделка раздела аналитики.

    Имена колонок — в нижнем регистре, как в живом ответе, а не как в истории
    торгов: на этом различии источник и был сломан.
    """

    def __init__(
        self,
        compositions: dict[str, list[tuple[str, str | None]]] | None = None,
        titles: dict[str, str] | None = None,
    ) -> None:
        self.compositions = compositions if compositions is not None else {}
        self.titles = TITLES if titles is None else titles
        self.requested: list[tuple[str, str | None]] = []

    async def fetch_index_titles(self) -> dict[str, str]:
        return self.titles

    async def fetch_index_analytics(
        self, index_id: str, session_date: str | None = None
    ) -> list[dict[str, object]]:
        self.requested.append((index_id, session_date))
        return [
            {
                "indexid": index_id,
                "tradedate": session_date or SESSION.isoformat(),
                "ticker": ticker,
                "weight": weight,
            }
            for ticker, weight in self.compositions.get(index_id, [])
        ]


async def _seed(session: AsyncSession, tickers: list[str]) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    for ticker in tickers:
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSION)
    await session.commit()
    return repository


# --- секторы (FR-020c) --------------------------------------------------------


async def test_sector_comes_from_the_industry_index(db_session: AsyncSession) -> None:
    """У биржи нет поля «сектор»: он выводится из принадлежности к индексу."""
    repository = await _seed(db_session, ["LKOH"])
    iss = FakeIss({"MOEXOG": [("LKOH", "21.94")]})

    written = await reference.sync_sectors(iss, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert written == 1
    assert await repository.sectors() == {"EQ_AST_LKOH": "Индекс нефти и газа"}


async def test_ticker_in_two_indices_takes_the_heavier(db_session: AsyncSession) -> None:
    """Бумага входит в несколько отраслевых индексов — берётся больший вес."""
    repository = await _seed(db_session, ["SBER"])
    iss = FakeIss(
        {
            "MOEXFN": [("SBER", "30.10")],
            "MOEXMM": [("SBER", "1.20")],
        }
    )

    await reference.sync_sectors(iss, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert await repository.sectors() == {"EQ_AST_SBER": "Индекс финансов"}


async def test_index_id_is_used_when_title_is_unknown(db_session: AsyncSession) -> None:
    """Имя индекса не выдумывается: при отсутствии берётся его код."""
    repository = await _seed(db_session, ["LKOH"])
    iss = FakeIss({"MOEXOG": [("LKOH", "21.94")]}, titles={})

    await reference.sync_sectors(iss, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert await repository.sectors() == {"EQ_AST_LKOH": "MOEXOG"}


async def test_all_eleven_indices_are_asked(db_session: AsyncSession) -> None:
    """Одиннадцать обращений на весь справочник, независимо от числа бумаг."""
    repository = await _seed(db_session, ["LKOH"])
    iss = FakeIss({"MOEXOG": [("LKOH", "21.94")]})

    await reference.sync_sectors(iss, repository, SESSION)  # type: ignore[arg-type]

    assert [index_id for index_id, _ in iss.requested] == list(reference.SECTOR_INDEX_IDS)


async def test_current_composition_is_asked_not_a_dated_one(
    db_session: AsyncSession,
) -> None:
    """Справочник без оси сессий: состав на дату ему не нужен."""
    repository = await _seed(db_session, ["LKOH"])
    iss = FakeIss({"MOEXOG": [("LKOH", "21.94")]})

    await reference.sync_sectors(iss, repository, SESSION)  # type: ignore[arg-type]

    assert all(day is None for _, day in iss.requested)


async def test_empty_sector_answer_is_a_failure(db_session: AsyncSession) -> None:
    """Ноль отраслей — неуспех с причиной, а не успешный пустой справочник."""
    repository = await _seed(db_session, ["LKOH"])

    with pytest.raises(reference.ReferenceEmptyError):
        await reference.sync_sectors(FakeIss(), repository, SESSION)  # type: ignore[arg-type]


async def test_ticker_without_weight_gets_no_sector(db_session: AsyncSession) -> None:
    """Строка без веса не назначает отрасль: это не наблюдение."""
    repository = await _seed(db_session, ["LKOH", "SBER"])
    iss = FakeIss({"MOEXOG": [("LKOH", "21.94"), ("SBER", None)]})

    await reference.sync_sectors(iss, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert "EQ_AST_SBER" not in await repository.sectors()


# --- веса в индексе (FR-020b) -------------------------------------------------


async def test_weights_are_collected_with_values(db_session: AsyncSession) -> None:
    repository = await _seed(db_session, ["SBER"])
    iss = FakeIss({"IMOEX": [("SBER", "13.87")]})

    result = await reference.sync_index_constituents(iss, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    assert result.rows_written == 1
    assert result.complete is True
    stored = await repository.global_values_for_window([SESSION])
    assert [(row.series_id, row.value) for row in stored] == [
        ("IDX_WEIGHT_IMOEX_SBER", Decimal("13.87"))
    ]


async def test_weights_are_asked_for_the_requested_date(db_session: AsyncSession) -> None:
    """У весов ось сессий есть, и состав берётся на дату."""
    repository = await _seed(db_session, ["SBER"])
    iss = FakeIss({"IMOEX": [("SBER", "13.87")]})

    await reference.sync_index_constituents(iss, repository, SESSION)  # type: ignore[arg-type]

    assert iss.requested == [("IMOEX", SESSION.isoformat())]


async def test_rows_without_a_single_weight_are_a_failure(
    db_session: AsyncSession,
) -> None:
    """Строки есть, весов нет — ровно тот дефект, что копил пустоту."""
    repository = await _seed(db_session, ["SBER"])
    iss = FakeIss({"IMOEX": [("SBER", None), ("GAZP", None)]})

    with pytest.raises(reference.ReferenceEmptyError):
        await reference.sync_index_constituents(iss, repository, SESSION)  # type: ignore[arg-type]


async def test_empty_composition_is_not_a_failure(db_session: AsyncSession) -> None:
    """Состава за дату нет вовсе — это отсутствие данных, а не сбой разбора."""
    repository = await _seed(db_session, ["SBER"])

    result = await reference.sync_index_constituents(FakeIss(), repository, SESSION)  # type: ignore[arg-type]

    assert result.rows_written == 0
    assert result.complete is True


async def test_no_empty_weight_rows_are_written(db_session: AsyncSession) -> None:
    """Пустые строки не пишутся: они заставляли считать сессию собранной."""
    repository = await _seed(db_session, ["SBER", "GAZP"])
    iss = FakeIss({"IMOEX": [("SBER", "13.87"), ("GAZP", None)]})

    await reference.sync_index_constituents(iss, repository, SESSION)  # type: ignore[arg-type]
    await db_session.commit()

    stored = await repository.global_values_for_window([SESSION])
    assert {row.series_id for row in stored} == {"IDX_WEIGHT_IMOEX_SBER"}
