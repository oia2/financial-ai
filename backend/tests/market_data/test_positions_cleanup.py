"""Тест разовой уборки пустых строк позиций (FR-020a, SC-009).

Уборка удаляет ровно то, что записал сломанный источник, и не трогает строки со
значениями. Различие проверяется, а не подразумевается: сгрести пустые заодно с
непустыми означало бы потерять настоящие данные, а восстановить их неоткуда —
источник отдаёт историю по обращению на инструмент и дату.

Тест выполняет **тот же запрос**, что и миграция, а не его пересказ: пересказ
разошёлся бы с миграцией молча.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.repository import MarketDataRepository, PositionRow

pytestmark = pytest.mark.db

SESSION = dt.date(2026, 8, 28)

MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0007_drop_empty_positions.py"
)


def _cleanup_sql() -> str:
    """Запрос уборки — из самой миграции."""
    spec = importlib.util.spec_from_file_location("migration_0007", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.DELETE_EMPTY_POSITIONS)


async def _seed(session: AsyncSession) -> None:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions([SESSION])
    for ticker in ("SBER", "GAZP", "LKOH"):
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSION)

    await repository.upsert_positions(
        [
            # Строка со значениями по всем сторонам.
            PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=SESSION,
                fiz_long=Decimal("125484"),
                fiz_short=Decimal("88768"),
                jur_long=Decimal("388054"),
                jur_short=Decimal("424770"),
            ),
            # Настоящая частичность: значение только по одной стороне.
            PositionRow(
                asset_id="EQ_AST_GAZP",
                session_date=SESSION,
                fiz_long=None,
                fiz_short=None,
                jur_long=Decimal("1"),
                jur_short=None,
            ),
            # То, что писал сломанный источник: ни одного значения.
            PositionRow(
                asset_id="EQ_AST_LKOH",
                session_date=SESSION,
                fiz_long=None,
                fiz_short=None,
                jur_long=None,
                jur_short=None,
            ),
        ]
    )
    await session.commit()


async def test_rows_without_any_value_are_removed(db_session: AsyncSession) -> None:
    """Строк без единого значения не остаётся."""
    await _seed(db_session)

    await db_session.execute(text(_cleanup_sql()))
    await db_session.commit()

    repository = MarketDataRepository(db_session)
    stored = await repository.positions_for_window([SESSION])
    assert "EQ_AST_LKOH" not in {row.asset_id for row in stored}


async def test_rows_with_values_are_kept(db_session: AsyncSession) -> None:
    """Строки со значениями дефектом не затронуты и остаются."""
    await _seed(db_session)

    await db_session.execute(text(_cleanup_sql()))
    await db_session.commit()

    repository = MarketDataRepository(db_session)
    stored = await repository.positions_for_window([SESSION])
    assert {row.asset_id for row in stored} == {"EQ_AST_SBER", "EQ_AST_GAZP"}


async def test_partial_coverage_survives_the_cleanup(db_session: AsyncSession) -> None:
    """Одно значение из четырёх — это данные, а не пустота."""
    await _seed(db_session)

    await db_session.execute(text(_cleanup_sql()))
    await db_session.commit()

    repository = MarketDataRepository(db_session)
    partial = next(
        row
        for row in await repository.positions_for_window([SESSION])
        if row.asset_id == "EQ_AST_GAZP"
    )
    assert partial.jur_long == Decimal("1")
    assert partial.fiz_long is None


async def test_cleaned_session_becomes_uncovered_for_that_asset(
    db_session: AsyncSession,
) -> None:
    """Цель уборки: сессия перестаёт числиться собранной и добирается заново.

    Оставленные пустые строки заставляли догон считать сессию закрытой, и
    правильные позиции за неё не появились бы никогда.
    """
    await _seed(db_session)
    repository = MarketDataRepository(db_session)
    # Ключ собранного — пара «актив — контракт»: строка, собранная другим
    # семейством, про текущее не говорит ничего (FR-039, T207).
    collected = await repository.positions_collected_on(SESSION)
    assert not any(asset_id == "EQ_AST_LKOH" for asset_id, _ in collected)

    await db_session.execute(text(_cleanup_sql()))
    await db_session.commit()

    assert {asset_id for asset_id, _ in await repository.positions_collected_on(SESSION)} == {
        "EQ_AST_SBER",
        "EQ_AST_GAZP",
    }


async def test_cleanup_is_repeatable(db_session: AsyncSession) -> None:
    """Повторное выполнение ничего не меняет: удалять уже нечего."""
    await _seed(db_session)

    await db_session.execute(text(_cleanup_sql()))
    await db_session.commit()
    repository = MarketDataRepository(db_session)
    after_first = {row.asset_id for row in await repository.positions_for_window([SESSION])}

    await db_session.execute(text(_cleanup_sql()))
    await db_session.commit()
    after_second = {row.asset_id for row in await repository.positions_for_window([SESSION])}

    assert after_first == after_second


# --- уборка пустых справочников (FR-020d) ------------------------------------
#
# Тот же класс, что и у позиций: строки писались, значений в них не было.
# Основание удаления то же — оставленные, они заставляют считать сессию
# собранной, и правильные веса за неё не появились бы никогда.

MIGRATION_0008 = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0008_drop_empty_reference.py"
)


def _reference_sql() -> tuple[str, str]:
    spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION_0008)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.DELETE_EMPTY_WEIGHTS), str(module.DELETE_EMPTY_SECTORS)


async def _seed_reference(session: AsyncSession) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions([SESSION])
    for ticker in ("SBER", "GAZP"):
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSION)

    await repository.upsert_global_values("IDX_WEIGHT_IMOEX_SBER", {SESSION: Decimal("13.87")})
    await repository.upsert_global_values("IDX_WEIGHT_IMOEX_GAZP", {SESSION: None})
    # Не вес: настоящий ряд с пропуском значения удаляться не должен.
    await repository.upsert_global_values("IMOEX", {SESSION: None})

    await repository.upsert_sectors({"EQ_AST_SBER": "Индекс финансов", "EQ_AST_GAZP": None})
    await session.commit()
    return repository


async def test_empty_weight_rows_are_removed(db_session: AsyncSession) -> None:
    repository = await _seed_reference(db_session)
    weights_sql, _ = _reference_sql()

    await db_session.execute(text(weights_sql))
    await db_session.commit()

    stored = {row.series_id for row in await repository.global_values_for_window([SESSION])}
    assert "IDX_WEIGHT_IMOEX_GAZP" not in stored
    assert "IDX_WEIGHT_IMOEX_SBER" in stored


async def test_other_series_keep_their_gaps(db_session: AsyncSession) -> None:
    """Пропуск в настоящем ряду — это данные: «мы не знаем», а не мусор."""
    repository = await _seed_reference(db_session)
    weights_sql, _ = _reference_sql()

    await db_session.execute(text(weights_sql))
    await db_session.commit()

    stored = {row.series_id for row in await repository.global_values_for_window([SESSION])}
    assert "IMOEX" in stored


async def test_sectors_without_a_value_are_removed(db_session: AsyncSession) -> None:
    repository = await _seed_reference(db_session)
    _, sectors_sql = _reference_sql()

    await db_session.execute(text(sectors_sql))
    await db_session.commit()

    assert await repository.sectors() == {"EQ_AST_SBER": "Индекс финансов"}


# --- уборка параметров модели вместо точек кривой ----------------------------
#
# Не пустая строка, а значение ДРУГОЙ величины под именем, похожим на точку
# кривой. Хуже пустоты: пустоту сводка показывает, подмену — нет.

MIGRATION_0009 = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "0009_drop_zcyc_model_parameter.py"
)


def _zcyc_sql() -> str:
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_0009)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.DELETE_ZCYC_MODEL_PARAMETERS)


async def _seed_zcyc(session: AsyncSession) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions([SESSION])
    await repository.upsert_global_values("CBR_ZCYC_B1", {SESSION: Decimal("7.1234")})
    await repository.upsert_global_values("CBR_ZCYC_yield_1y", {SESSION: Decimal("13.64")})
    await repository.upsert_global_values("CBR_KEY_RATE", {SESSION: Decimal("14.00")})
    await session.commit()
    return repository


async def test_model_parameter_series_is_removed(db_session: AsyncSession) -> None:
    repository = await _seed_zcyc(db_session)

    await db_session.execute(text(_zcyc_sql()))
    await db_session.commit()

    stored = {row.series_id for row in await repository.global_values_for_window([SESSION])}
    assert "CBR_ZCYC_B1" not in stored


async def test_curve_points_are_kept(db_session: AsyncSession) -> None:
    """Настоящие точки по срокам не трогаются."""
    repository = await _seed_zcyc(db_session)

    await db_session.execute(text(_zcyc_sql()))
    await db_session.commit()

    stored = {row.series_id for row in await repository.global_values_for_window([SESSION])}
    assert "CBR_ZCYC_yield_1y" in stored
    assert "CBR_KEY_RATE" in stored
