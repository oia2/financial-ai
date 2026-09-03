"""Активы, переданные в запросе.

Раньше вселенная бралась из конфигурации эмулятора. Теперь активы приходят в
запросе — так же, как их будет получать обученная модель: список из конфигурации
не имел отношения к тому, что торговалось на дату решения.

Проверки при этом никуда не делись, они переехали со старта на запрос: пустой
список и дубликаты по-прежнему отвергаются. Ключ `asof_date + asset_id` обязан
остаться ключом.
"""

from __future__ import annotations

from dataclasses import dataclass

# Верхняя граница. Взята из SC-002 спецификации эмулятора: ответ обязан
# приходить быстрее секунды.
MAX_ASSET_COUNT = 1000


class UniverseError(ValueError):
    """Перечень активов в запросе непригоден."""


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    """Один актив.

    Идентичность повторяет каноническую в исследовательском репозитории:
    `asset_id` — экономический актив, устойчивый к переименованиям тикера,
    `price_series_id` — сшиваемый ценовой ряд этого актива.
    """

    asset_id: str
    price_series_id: str


def entries_from_request(assets: list[dict[str, str]]) -> tuple[UniverseEntry, ...]:
    """Проверить и преобразовать активы запроса.

    Отказ здесь лучше молчаливого ранжирования непригодного списка: пустой
    ответ или две строки на один актив обнаружились бы гораздо позже.
    """
    if not assets:
        raise UniverseError("перечень активов пуст: ранжировать нечего")

    if len(assets) > MAX_ASSET_COUNT:
        raise UniverseError(f"передано {len(assets)} активов, допустимо не более {MAX_ASSET_COUNT}")

    entries: list[UniverseEntry] = []
    seen_assets: set[str] = set()
    seen_series: set[str] = set()

    for position, item in enumerate(assets):
        asset_id = _require(item, "asset_id", position)
        price_series_id = _require(item, "price_series_id", position)

        if asset_id in seen_assets:
            raise UniverseError(
                f"asset_id {asset_id} передан более одного раза (запись {position}); "
                "ключ asof_date + asset_id перестал бы быть ключом"
            )
        if price_series_id in seen_series:
            raise UniverseError(
                f"price_series_id {price_series_id} передан более одного раза (запись {position})"
            )

        seen_assets.add(asset_id)
        seen_series.add(price_series_id)
        entries.append(UniverseEntry(asset_id=asset_id, price_series_id=price_series_id))

    return tuple(entries)


def _require(item: dict[str, str], field: str, position: int) -> str:
    value = item.get(field)
    if value is None:
        raise UniverseError(f"в записи {position} отсутствует поле {field}")
    if not isinstance(value, str) or not value.strip():
        raise UniverseError(f"поле {field} в записи {position} пустое")
    return value
