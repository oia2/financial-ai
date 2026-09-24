"""Тесты реестра групп источников.

Группа — единица выбора при запуске догона и единица строки в сводке. Здесь
проверяется, что реестр описывает именно то, чем оперирует модель, и что у
каждой группы есть способ отличить пустую строку от заполненной.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.config import Settings
from financial_ai.market_data import groups
from financial_ai.market_data.groups import GroupId, UnknownGroupError


def test_groups_are_declared() -> None:
    """Семейства входов модели и группы фондов, а не отдельные источники (FR-060a)."""
    assert {group.group_id for group in groups.GROUPS} == {
        GroupId.QUOTES,
        GroupId.AGGREGATES,
        GroupId.GLOBAL,
        GroupId.POSITIONS,
        GroupId.FUND_QUOTES,
        GroupId.FUND_AGGREGATES,
        GroupId.REFERENCE,
    }


def test_fund_groups_share_sources_and_stay_out_of_the_model() -> None:
    """Фонды — те же источники доски, окно с 22.06.2026, вход модели — нет (FR-060)."""
    by_id = groups.BY_ID
    assert by_id[GroupId.FUND_QUOTES].source_ids == by_id[GroupId.QUOTES].source_ids
    assert by_id[GroupId.FUND_AGGREGATES].source_ids == by_id[GroupId.AGGREGATES].source_ids
    for group_id in (GroupId.FUND_QUOTES, GroupId.FUND_AGGREGATES):
        group = by_id[group_id]
        assert not group.model_input
        assert group.available_from == dt.date(2026, 6, 22)
        assert group.trim([dt.date(2026, 6, 19), dt.date(2026, 6, 22)]) == [dt.date(2026, 6, 22)]


def test_fund_groups_are_never_required_by_the_model() -> None:
    """Даже названная в настройке группа фондов обязательной не становится (FR-060c)."""
    settings = Settings(daily_ml_required_data_groups=["quotes", "fund_quotes"])
    assert [group.group_id for group in groups.required(settings)] == [GroupId.QUOTES]


def test_every_group_declares_how_to_tell_a_row_is_filled() -> None:
    """Без этого сводка не отличит собранное от собранного пустым."""
    for group in groups.GROUPS:
        assert group.value_columns, f"у группы {group.group_id} нет столбцов значений"


def test_positions_accept_any_of_four_sides() -> None:
    """Покрытие по сторонам бывает частичным по-настоящему.

    Это не то же самое, что пустая строка: наличие хотя бы одной стороны
    означает, что данные пришли.
    """
    positions = groups.BY_ID[GroupId.POSITIONS]
    assert set(positions.value_columns) == {"fiz_long", "fiz_short", "jur_long", "jur_short"}


def test_reference_has_no_session_axis() -> None:
    """Справочник текущего состояния не бывает недобранным."""
    reference = groups.BY_ID[GroupId.REFERENCE]
    assert reference.has_history is False
    assert reference.window_sessions(Settings()) is None


def test_groups_with_history_have_a_window() -> None:
    for group in groups.GROUPS:
        if group.has_history:
            assert group.window_sessions(Settings()) is not None


def test_windows_follow_the_model_configuration() -> None:
    """Глубины взяты из настроек окон, а не назначены группе отдельно."""
    settings = Settings(
        market_data_price_window_sessions=314,
        market_data_global_window_sessions=300,
        market_data_positions_window_sessions=82,
    )
    assert groups.BY_ID[GroupId.QUOTES].window_sessions(settings) == 314
    assert groups.BY_ID[GroupId.AGGREGATES].window_sessions(settings) == 314
    assert groups.BY_ID[GroupId.GLOBAL].window_sessions(settings) == 300
    assert groups.BY_ID[GroupId.POSITIONS].window_sessions(settings) == 82


def test_global_group_gathers_all_daily_series() -> None:
    """Индексы, ЦБ, Brent и веса в индексе для человека — одна сущность."""
    global_group = groups.BY_ID[GroupId.GLOBAL]
    assert {"global_series", "cbr", "brent", "index_constituents"} <= set(global_group.source_ids)


def test_empty_selection_means_all_groups() -> None:
    assert groups.resolve(None) == groups.GROUPS
    assert groups.resolve([]) == groups.GROUPS


def test_selection_is_resolved_by_name() -> None:
    selected = groups.resolve(["quotes", "positions"])
    assert [group.group_id for group in selected] == [GroupId.QUOTES, GroupId.POSITIONS]


def test_unknown_group_is_rejected_with_the_known_list() -> None:
    """Сообщение перечисляет известные: иначе выбор превращается в угадывание."""
    with pytest.raises(UnknownGroupError) as error:
        groups.resolve(["котировки"])
    assert "quotes" in str(error.value)


def test_source_ids_are_collected_across_groups() -> None:
    selected = groups.resolve(["quotes", "aggregates"])
    assert groups.source_ids_for(selected) == frozenset({"equity_d1", "equity_agg"})
