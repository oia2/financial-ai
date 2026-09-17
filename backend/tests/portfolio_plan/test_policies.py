"""Правила распределения — specs/007-daily-ml-lifecycle/data-model.md §7.

Веса перенесены из исследования, а не выведены здесь. Проверяется именно
перенос: сумма единица, доли диапазонов те самые, внутри диапазона поровну.
Ошибка в четвёртом знаке не видна глазом и меняет состав портфеля.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from financial_ai.portfolio_plan import policies


def test_every_policy_distributes_exactly_one_capital() -> None:
    """Сумма весов — единица. Иначе часть капитала теряется или удваивается."""
    for policy in policies.POLICIES:
        weights = policy.weights()
        assert len(weights) == policy.asset_count
        assert sum(weights) == pytest.approx(Decimal(1), abs=Decimal("0.0000000001"))


def test_equal_policy_gives_every_asset_the_same_share() -> None:
    weights = policies.BY_ID["equal_top20"].weights()

    assert len(set(weights)) == 1
    assert weights[0] == Decimal(1) / Decimal(20)


def test_equal_sizes_match_the_research_set() -> None:
    """Набор k задан исследованием: произвольное число человеком не вводится."""
    assert policies.EQUAL_SIZES == (20, 30, 50, 100, 150)
    for size in policies.EQUAL_SIZES:
        assert f"equal_top{size}" in policies.BY_ID


def test_rank_zones_carry_the_published_weights() -> None:
    """Диапазоны 1–20, 21–30, 31–50, 51–100 с долями из handoff §10.3."""
    weights = policies.BY_ID["rank_zones_100"].weights()

    assert sum(weights[:20]) == Decimal("0.2403")
    assert sum(weights[20:30]) == Decimal("0.1063")
    assert sum(weights[30:50]) == Decimal("0.2039")
    assert sum(weights[50:100]) == Decimal("0.4495")


def test_inside_a_zone_the_weight_is_split_evenly() -> None:
    weights = policies.BY_ID["rank_zones_100"].weights()

    assert len(set(weights[:20])) == 1
    assert weights[0] == Decimal("0.2403") / Decimal(20)
    # Последний диапазон шире и потому на одно имя даёт меньше, чем первый,
    # несмотря на больший вес диапазона. Это свойство правила, не опечатка.
    assert weights[50] == Decimal("0.4495") / Decimal(50)
    assert weights[50] < weights[0]


def test_unknown_policy_is_none_not_a_default() -> None:
    """Неизвестное правило не подменяется умолчанием: подмена сменила бы состав."""
    assert policies.get("equal_top7") is None
    assert policies.get(policies.DEFAULT_POLICY) is not None
