"""Правила распределения весов по рангам.

Перенесены из исследовательского репозитория, а не придуманы здесь: веса
диапазонов взяты из handoff §10.3 и проверены в
`strategy_registry/moex_long_only_momentum/search_restored_winner_interval_upgrades_fixed.py`
(область строки 468). Равные доли по первым k — §10.1.

Правило выбирает **только веса**. Состав определяется порядком ранжирования, и
модель о правиле ничего не знает: смена правила не требует обращения к ней
(FR-064). Поэтому перечень задаётся сервером и произвольное правило человеком
не вводится — иначе в плане появились бы веса, не проверенные исследованием.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Равные доли: набор k из исследования. Больше 150 не предлагается — на
# российском рынке ликвидных имён меньше.
EQUAL_SIZES = (20, 30, 50, 100, 150)

# Диапазоны рангов 1–100 и их доли капитала. Сумма — единица; внутри диапазона
# вес делится поровну. Числа перенесены дословно: округление «на глаз» сдвинуло
# бы состав относительно проверенного правила.
RANK_ZONES: tuple[tuple[int, int, Decimal], ...] = (
    (1, 20, Decimal("0.2403")),
    (21, 30, Decimal("0.1063")),
    (31, 50, Decimal("0.2039")),
    (51, 100, Decimal("0.4495")),
)

DEFAULT_POLICY = "equal_top20"


@dataclass(frozen=True, slots=True)
class Policy:
    """Правило распределения: сколько активов берётся и с какими весами."""

    id: str
    title: str
    asset_count: int
    description: str
    source: str

    def weights(self) -> list[Decimal]:
        """Веса по местам, от первого к последнему."""
        if self.id == "rank_zones_100":
            return _zone_weights()
        return _equal_weights(self.asset_count)


def _equal_weights(count: int) -> list[Decimal]:
    share = Decimal(1) / Decimal(count)
    return [share] * count


def _zone_weights() -> list[Decimal]:
    weights: list[Decimal] = []
    for first, last, zone_weight in RANK_ZONES:
        size = last - first + 1
        weights.extend([zone_weight / Decimal(size)] * size)
    return weights


def _equal_policy(count: int) -> Policy:
    return Policy(
        id=f"equal_top{count}",
        title=f"Равные доли · первые {count}",
        asset_count=count,
        description=f"Первые {count} активов ранжирования, равными долями расчётного капитала.",
        source="handoff §10.1",
    )


ZONES_POLICY = Policy(
    id="rank_zones_100",
    title="По диапазонам рангов · первые 100",
    asset_count=100,
    description=(
        "Ранги 1–20: 24,03%; 21–30: 10,63%; 31–50: 20,39%; 51–100: 44,95%. "
        "Внутри диапазона — поровну."
    ),
    source="handoff §10.3",
)

POLICIES: tuple[Policy, ...] = (*(_equal_policy(size) for size in EQUAL_SIZES), ZONES_POLICY)

BY_ID = {policy.id: policy for policy in POLICIES}


def get(policy_id: str) -> Policy | None:
    return BY_ID.get(policy_id)
