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

import pytest

from financial_ai.market_data.iss.client import ResponseContractError
from financial_ai.market_data.sources.equity_agg import rows_to_aggregates
from financial_ai.market_data.sources.global_series import ISS_SERIES, rows_to_values
from financial_ai.market_data.sources.reference import (
    INDEX_WEIGHT_PREFIX,
    SECTOR_INDEX_IDS,
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


def test_aggregate_row_without_secid_is_contract_violation() -> None:
    """Пропущенная молча, строка без бумаги давала полноту доски (FR-032e)."""
    with pytest.raises(ResponseContractError):
        rows_to_aggregates([{"SECID": "  ", "VALUE": "1"}], SESSION)


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
#
# Колонок `SECTORID`/`SECTORNAME` у биржи нет ни в одном разделе: сектор
# выводится из принадлежности к отраслевым индексам. Прежние тесты проверяли
# разбор выдуманных колонок и потому проходили, пока справочник был пуст.


def test_sector_indices_match_the_original() -> None:
    """Перечень отраслевых индексов перенесён, а не собран по догадке."""
    assert SECTOR_INDEX_IDS[:3] == ("MOEXOG", "MOEXMM", "MOEXFN")
    assert len(SECTOR_INDEX_IDS) == 11
    assert all(index_id.startswith("MOEX") for index_id in SECTOR_INDEX_IDS)


# --- состав индекса ---------------------------------------------------------
#
# Имена колонок раздела аналитики — в нижнем регистре, в отличие от истории
# торгов. Образец снят с живого ответа 2026-09-04.


def _row(ticker: str, weight: str | None, day: str = "2026-08-28") -> dict[str, object]:
    return {
        "indexid": "IMOEX",
        "tradedate": day,
        "ticker": ticker,
        "shortnames": "Сбербанк",
        "secids": ticker,
        "weight": weight,
    }


def test_weight_series_id_carries_index_and_ticker() -> None:
    out = rows_to_weights([_row("SBER", "13.87")], SESSION, "IMOEX")
    assert list(out) == [f"{INDEX_WEIGHT_PREFIX}IMOEX_SBER"]
    assert out[f"{INDEX_WEIGHT_PREFIX}IMOEX_SBER"] == {SESSION: Decimal("13.87")}


def test_weight_precision_is_exact() -> None:
    """Вес — доля процента: float исказил бы её последними знаками."""
    out = rows_to_weights([_row("SBER", "13.876543210")], SESSION, "IMOEX")
    assert out[f"{INDEX_WEIGHT_PREFIX}IMOEX_SBER"][SESSION] == Decimal("13.876543210")


def test_absent_ticker_produces_no_series() -> None:
    """Выбывшая из индекса бумага — не бумага с нулевым весом."""
    assert rows_to_weights([], SESSION, "IMOEX") == {}


def test_row_without_weight_is_dropped() -> None:
    """Пустая строка веса — не наблюдение.

    Записанная, она выглядела бы собранной и не дала бы догону вернуться за
    настоящим весом. Так и накопились 62 584 строки без единого значения.
    """
    assert rows_to_weights([_row("SBER", None)], SESSION, "IMOEX") == {}


def test_snapshot_for_another_date_is_dropped() -> None:
    """Раздел отдаёт ближайший доступный состав — передатировать его нельзя."""
    out = rows_to_weights([_row("SBER", "13.87", day="2026-08-27")], SESSION, "IMOEX")
    assert out == {}


def test_uppercase_history_columns_are_not_read() -> None:
    """Именно на этом источник и был сломан: он просил колонки истории торгов."""
    rows = [{"SECID": "SBER", "TRADEDATE": "2026-08-28", "WEIGHT": "13.87"}]
    # Строка без колонки раздела аналитики — чужой контракт, а не пустой состав
    # (FR-032e): прежде она молча давала ноль весов.
    with pytest.raises(ResponseContractError):
        rows_to_weights(rows, SESSION, "IMOEX")
