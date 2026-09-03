"""Правило ранжирования эмулятора.

Здесь нет ни модели, ни рыночных данных, ни признаков, ни весов, ни временных окон.
Порядок активов задаётся циклическим сдвигом настроенной вселенной, а величина сдвига —
датой решения:

    N       = число активов во вселенной
    offset  = ordinal(decision_date) mod N
    rank(i) = ((i + offset) mod N) + 1
    score   = 1 - rank / (N + 1)

Правило выбрано таким, чтобы одновременно выполнялись два требования: одна и та же дата
всегда даёт один и тот же ответ (в том числе после перезапуска), а разные даты дают
разный порядок. Оно зависит только от даты и конфигурации — ни от времени запроса, ни от
состояния процесса, ни от генератора случайных чисел.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from daily_ml_emulator.universe import UniverseEntry

# Знаков после запятой в скоре. Значение фиксировано: скор передаётся строкой, и
# потребитель должен получать ровно то, что выдано.
SCORE_EXPONENT = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class RankingItem:
    """Одна позиция ранжирования.

    Осмыслен только порядок внутри одной даты решения: абсолютное значение `score`
    интерпретации не имеет и сравнению между датами не подлежит. Это свойство настоящей
    модели, обучаемой pairwise ranking objective, а не упрощение эмулятора.
    """

    rank: int
    asset_id: str
    price_series_id: str
    score: str


def score_for_rank(rank: int, universe_size: int) -> str:
    """Скор для места `rank` при вселенной размера `universe_size`.

    Считается в `Decimal` и возвращается строкой: `float` на этом пути запрещён — по
    тому же правилу, по которому в проекте передаются денежные величины.
    """
    value = Decimal(1) - Decimal(rank) / Decimal(universe_size + 1)
    return str(value.quantize(SCORE_EXPONENT, rounding=ROUND_HALF_UP))


def build_ranking(
    universe: tuple[UniverseEntry, ...], decision_date: date
) -> tuple[RankingItem, ...]:
    """Построить ранжирование всей вселенной на дату решения."""
    size = len(universe)
    offset = decision_date.toordinal() % size

    items = [
        RankingItem(
            rank=((position + offset) % size) + 1,
            asset_id=entry.asset_id,
            price_series_id=entry.price_series_id,
            score=score_for_rank(((position + offset) % size) + 1, size),
        )
        for position, entry in enumerate(universe)
    ]

    # Вторичный ключ `asset_id` при текущем правиле ни на что не влияет: скоры различны
    # по построению, поэтому ничьих не бывает. Он существует, чтобы порядок не зависел
    # от порядка обхода структуры данных, если правило когда-нибудь изменят.
    items.sort(key=lambda item: (item.rank, item.asset_id))
    return tuple(items)
