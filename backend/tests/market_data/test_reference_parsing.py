"""Тесты разбора агрегатов, глобальных рядов и справочников.

Имена колонок ISS перенесены из исходных пайплайнов `MR-MASTER-DRO`, а ответы
биржи в этой среде получить нельзя. Поэтому проверяется перенос: разбор на
образце ожидаемой структуры. Без этих тестов опечатка в имени колонки прошла бы
незамеченной — источник просто вернул бы пустоту, и это выглядело бы как
отсутствие торгов.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from financial_ai.market_data.sources.equity_agg import rows_to_aggregates
from financial_ai.market_data.sources.global_series import ISS_SERIES, rows_to_values
from financial_ai.market_data.sources.reference import (
    INDEX_WEIGHT_PREFIX,
    rows_to_sectors,
    rows_to_weights,
)

SESSION = dt.date(2026, 8, 28)


# --- агрегаты ---------------------------------------------------------------


def test_aggregate_columns_are_read() -> None:
    rows = [{"SECID": "SBER", "VALUE": "1234567.89", "NUMTRADES": "4211", "WAPRICE": "313.98"}]
    aggregate = rows_to_aggregates(rows, SESSION)[0]
    assert aggregate.value == Decimal("1234567.89")
    assert aggregate.num_trades == Decimal("4211")
    assert aggregate.waprice == Decimal("313.98")


def test_aggregate_precision_is_exact() -> None:
    """`waprice` — средневзвешенная цена: float исказил бы её последними знаками."""
    rows = [{"SECID": "SBER", "VALUE": "1", "NUMTRADES": "1", "WAPRICE": "313.987654321"}]
    assert rows_to_aggregates(rows, SESSION)[0].waprice == Decimal("313.987654321")


def test_aggregate_missing_value_is_none_not_zero() -> None:
    """Бумага могла не торговаться: это не нулевой оборот."""
    rows = [{"SECID": "SBER", "VALUE": None, "NUMTRADES": "", "WAPRICE": "313.98"}]
    aggregate = rows_to_aggregates(rows, SESSION)[0]
    assert aggregate.value is None
    assert aggregate.num_trades is None


def test_aggregate_keeps_session_from_argument() -> None:
    """Дата берётся из аргумента, а не из ответа: передатирование запрещено."""
    rows = [{"SECID": "SBER", "TRADEDATE": "2026-08-27", "VALUE": "1"}]
    assert rows_to_aggregates(rows, SESSION)[0].session_date == SESSION


def test_aggregate_duplicate_ticker_is_taken_once() -> None:
    rows = [{"SECID": "SBER", "VALUE": "1"}, {"SECID": "sber", "VALUE": "2"}]
    assert len(rows_to_aggregates(rows, SESSION)) == 1


def test_aggregate_row_without_secid_is_dropped() -> None:
    assert rows_to_aggregates([{"SECID": "  ", "VALUE": "1"}], SESSION) == []


# --- глобальные ряды --------------------------------------------------------


def test_global_value_is_read_by_declared_column() -> None:
    rows = [{"SECID": "IMOEX", "TRADEDATE": "2026-08-28", "CLOSE": "3200.55"}]
    assert rows_to_values(rows, "CLOSE") == {SESSION: Decimal("3200.55")}


def test_global_row_without_date_is_dropped() -> None:
    """Значение без даты некуда положить: канонической осью служит календарь."""
    assert rows_to_values([{"SECID": "IMOEX", "CLOSE": "3200.55"}], "CLOSE") == {}


def test_global_missing_value_is_none_not_zero() -> None:
    rows = [{"TRADEDATE": "2026-08-28", "CLOSE": None}]
    assert rows_to_values(rows, "CLOSE") == {SESSION: None}


def test_declared_series_cover_the_indices_the_model_reads() -> None:
    """`global_regime_core_v1.yaml` объявляет index_ids: MOEX, RTSI, RGBI, RVI."""
    declared = {spec.series_id for spec in ISS_SERIES}
    assert {"IMOEX", "RTSI", "RGBI", "RVI"} <= declared


def test_index_series_live_in_the_index_market() -> None:
    """Раздел торгов взят из конфигурации оригинала, а не подобран."""
    imoex = next(spec for spec in ISS_SERIES if spec.series_id == "IMOEX")
    assert (imoex.engine, imoex.market) == ("stock", "index")


# --- секторы ----------------------------------------------------------------


def test_sector_name_is_preferred_over_id() -> None:
    rows = [{"SECID": "SBER", "SECTORID": "FIN", "SECTORNAME": "Финансы"}]
    assert rows_to_sectors(rows) == {"EQ_AST_SBER": "Финансы"}


def test_sector_falls_back_to_id() -> None:
    rows = [{"SECID": "SBER", "SECTORID": "FIN", "SECTORNAME": ""}]
    assert rows_to_sectors(rows) == {"EQ_AST_SBER": "FIN"}


def test_unknown_sector_is_none_not_placeholder() -> None:
    """Строка-заглушка попала бы в признаки как настоящая категория."""
    rows = [{"SECID": "SBER", "SECTORID": "", "SECTORNAME": ""}]
    assert rows_to_sectors(rows) == {"EQ_AST_SBER": None}


# --- состав индекса ---------------------------------------------------------


def test_weight_series_id_carries_index_and_ticker() -> None:
    rows = [{"SECID": "SBER", "TRADEDATE": "2026-08-28", "WEIGHT": "13.87"}]
    out = rows_to_weights(rows, SESSION, "IMOEX")
    assert list(out) == [f"{INDEX_WEIGHT_PREFIX}IMOEX_SBER"]
    assert out[f"{INDEX_WEIGHT_PREFIX}IMOEX_SBER"] == {SESSION: Decimal("13.87")}


def test_absent_ticker_produces_no_series() -> None:
    """Выбывшая из индекса бумага — не бумага с нулевым весом."""
    assert rows_to_weights([], SESSION, "IMOEX") == {}


def test_weight_date_falls_back_to_session() -> None:
    rows = [{"SECID": "SBER", "WEIGHT": "13.87"}]
    out = rows_to_weights(rows, SESSION, "IMOEX")
    assert out[f"{INDEX_WEIGHT_PREFIX}IMOEX_SBER"] == {SESSION: Decimal("13.87")}
