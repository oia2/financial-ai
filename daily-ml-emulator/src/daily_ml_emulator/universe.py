"""Вселенная активов: то, что эмулятор ранжирует.

Аналог того, что настоящая модель определяет сама на каждую дату. Здесь она задаётся
конфигурацией и загружается один раз при старте.

Порядок записей значим: он задаёт базовую нумерацию, от которой отсчитывается сдвиг
в правиле ранжирования. Больше ни на что порядок не влияет.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Верхняя граница вселенной. Взята из SC-002: ответ обязан приходить быстрее секунды
# при вселенной до 200 активов.
MAX_UNIVERSE_SIZE = 200


class UniverseError(RuntimeError):
    """Конфигурация вселенной непригодна.

    Поднимается при старте и не даёт контейнеру подняться. Отвечать 500 на каждый
    запрос при заведомо сломанной конфигурации хуже: неисправность обнаружилась бы
    позже и дальше от причины.
    """


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    """Один актив вселенной.

    Идентичность повторяет каноническую в исследовательском репозитории:
    `asset_id` — экономический актив, устойчивый к переименованиям тикера,
    `price_series_id` — сшиваемый ценовой ряд этого актива.
    """

    asset_id: str
    price_series_id: str


def _read_document(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UniverseError(f"не удалось прочитать файл вселенной {path}: {error}") from error

    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise UniverseError(
            f"файл вселенной {path} не является корректным JSON: {error}"
        ) from error


def _require_field(item: dict[str, Any], field: str, position: int, path: Path) -> str:
    """Достать непустое строковое поле записи или отказать с внятным сообщением."""
    value = item.get(field)
    if value is None:
        raise UniverseError(f"в записи {position} в {path} отсутствует поле {field}")
    if not isinstance(value, str):
        raise UniverseError(
            f"поле {field} в записи {position} в {path} должно быть строкой, "
            f"получен {type(value).__name__}"
        )
    if not value.strip():
        raise UniverseError(f"поле {field} в записи {position} в {path} пустое")
    return value


def load_universe(path: Path) -> tuple[UniverseEntry, ...]:
    """Прочитать вселенную из JSON-файла и проверить её пригодность."""
    document = _read_document(path)

    if not isinstance(document, list):
        raise UniverseError(
            f"файл вселенной {path} должен содержать список активов, "
            f"получен {type(document).__name__}"
        )

    if not document:
        raise UniverseError(
            f"вселенная в {path} пуста: ранжировать нечего. Нужен хотя бы один актив"
        )

    if len(document) > MAX_UNIVERSE_SIZE:
        raise UniverseError(
            f"во вселенной {path} {len(document)} активов, допустимо не более {MAX_UNIVERSE_SIZE}"
        )

    entries: list[UniverseEntry] = []
    seen_asset_ids: set[str] = set()
    seen_price_series_ids: set[str] = set()

    for position, item in enumerate(document):
        if not isinstance(item, dict):
            raise UniverseError(
                f"запись {position} в {path} должна быть объектом, получен {type(item).__name__}"
            )

        asset_id = _require_field(item, "asset_id", position, path)
        price_series_id = _require_field(item, "price_series_id", position, path)

        # Уникальность asset_id — то, на чём держится ключ decision_date + asset_id.
        if asset_id in seen_asset_ids:
            raise UniverseError(
                f"asset_id {asset_id} встречается во вселенной {path} более одного раза "
                f"(запись {position}); ключ decision_date + asset_id перестал бы быть ключом"
            )
        if price_series_id in seen_price_series_ids:
            raise UniverseError(
                f"price_series_id {price_series_id} встречается во вселенной {path} "
                f"более одного раза (запись {position})"
            )

        seen_asset_ids.add(asset_id)
        seen_price_series_ids.add(price_series_id)
        entries.append(UniverseEntry(asset_id=asset_id, price_series_id=price_series_id))

    return tuple(entries)
