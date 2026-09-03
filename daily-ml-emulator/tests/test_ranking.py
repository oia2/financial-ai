"""Тесты правила ранжирования.

Правило описано в specs/002-daily-ml-emulator/data-model.md §4. Содержательных
вычислений в нём нет: это циклический сдвиг вселенной, величина сдвига — от даты.
"""

from datetime import date

import pytest

from daily_ml_emulator.ranking import build_ranking, score_for_rank
from daily_ml_emulator.universe import UniverseEntry

UNIVERSE = tuple(
    UniverseEntry(asset_id=f"EQ_AST_{ticker}", price_series_id=f"EQ_PRS_{ticker}")
    for ticker in (
        "SBER",
        "GAZP",
        "LKOH",
        "GMKN",
        "ROSN",
        "NVTK",
        "TATN",
        "MTSS",
        "MGNT",
        "CHMF",
    )
)


# --- T011: правило сдвига, ранги, лестница скоров ---------------------------------


def test_ranking_contains_whole_universe() -> None:
    items = build_ranking(UNIVERSE, date(2026, 8, 28))
    assert len(items) == len(UNIVERSE)
    assert {item.asset_id for item in items} == {entry.asset_id for entry in UNIVERSE}


def test_ranks_are_contiguous_from_one() -> None:
    items = build_ranking(UNIVERSE, date(2026, 8, 28))
    assert [item.rank for item in items] == list(range(1, len(UNIVERSE) + 1))


def test_offset_follows_ordinal_of_decision_date() -> None:
    """rank(i) = ((i + offset) mod N) + 1, где offset = ordinal(date) mod N."""
    decision_date = date(2026, 8, 28)
    size = len(UNIVERSE)
    offset = decision_date.toordinal() % size

    items = build_ranking(UNIVERSE, decision_date)
    rank_by_asset = {item.asset_id: item.rank for item in items}

    for position, entry in enumerate(UNIVERSE):
        assert rank_by_asset[entry.asset_id] == ((position + offset) % size) + 1


def test_scores_strictly_decrease_with_rank() -> None:
    from decimal import Decimal

    items = build_ranking(UNIVERSE, date(2026, 8, 28))
    scores = [Decimal(item.score) for item in items]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores), "скоры должны быть различны — ничьих не бывает"


def test_score_ladder_for_ten_assets() -> None:
    expected = [
        "0.9091",
        "0.8182",
        "0.7273",
        "0.6364",
        "0.5455",
        "0.4545",
        "0.3636",
        "0.2727",
        "0.1818",
        "0.0909",
    ]
    assert [score_for_rank(rank, 10) for rank in range(1, 11)] == expected


def test_score_is_string_not_number() -> None:
    items = build_ranking(UNIVERSE, date(2026, 8, 28))
    assert all(isinstance(item.score, str) for item in items)


def test_price_series_id_is_carried_from_universe() -> None:
    items = build_ranking(UNIVERSE, date(2026, 8, 28))
    expected = {entry.asset_id: entry.price_series_id for entry in UNIVERSE}
    assert all(item.price_series_id == expected[item.asset_id] for item in items)


def test_single_asset_universe() -> None:
    universe = (UniverseEntry(asset_id="EQ_AST_SBER", price_series_id="EQ_PRS_SBER"),)
    items = build_ranking(universe, date(2026, 8, 28))
    assert len(items) == 1
    assert items[0].rank == 1


# --- T012: детерминизм и зависимость от даты --------------------------------------


def test_same_date_gives_same_result() -> None:
    first = build_ranking(UNIVERSE, date(2026, 8, 28))
    second = build_ranking(UNIVERSE, date(2026, 8, 28))
    assert first == second


def test_repeated_calls_are_stable() -> None:
    reference = build_ranking(UNIVERSE, date(2026, 8, 28))
    for _ in range(100):
        assert build_ranking(UNIVERSE, date(2026, 8, 28)) == reference


def test_different_dates_give_different_order() -> None:
    first = build_ranking(UNIVERSE, date(2026, 8, 28))
    second = build_ranking(UNIVERSE, date(2026, 8, 29))
    assert [item.asset_id for item in first] != [item.asset_id for item in second]


@pytest.mark.parametrize(
    ("decision_date", "expected_leader"),
    [
        (date(2026, 8, 28), "EQ_AST_ROSN"),
        (date(2026, 8, 29), "EQ_AST_GMKN"),
        (date(2026, 8, 31), "EQ_AST_GAZP"),
    ],
)
def test_known_leaders_for_fixed_dates(decision_date: date, expected_leader: str) -> None:
    """Значения зафиксированы в data-model.md §4 и quickstart.md, сценарий С3."""
    items = build_ranking(UNIVERSE, decision_date)
    assert items[0].asset_id == expected_leader


def test_result_does_not_depend_on_current_time() -> None:
    """Ранжирование зависит только от даты решения и вселенной.

    Если бы в правило попало текущее время, тест ниже был бы нестабилен.
    """
    import time

    first = build_ranking(UNIVERSE, date(2026, 8, 28))
    time.sleep(0.01)
    second = build_ranking(UNIVERSE, date(2026, 8, 28))
    assert first == second


def test_dates_a_universe_size_apart_repeat_the_order() -> None:
    """Следствие сдвига по модулю N: через N дней порядок повторяется.

    Свойство самого правила, а не требование спецификации; фиксируется, чтобы
    изменение правила не прошло незамеченным.
    """
    first = build_ranking(UNIVERSE, date(2026, 8, 28))
    later = build_ranking(UNIVERSE, date(2026, 9, 7))  # +10 дней при N = 10
    assert [item.asset_id for item in first] == [item.asset_id for item in later]
