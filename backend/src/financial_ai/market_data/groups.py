"""Группы источников: единица выбора и единица отчёта.

Группа — это семейство входов модели, а не отдельный источник. Так рассуждает
модель (`configs/features/*`), и так человек задаёт вопрос «что у нас есть».
Деление по одному источнику дало бы двенадцать пунктов, пять из которых для
человека — одни «глобальные ряды».

У каждой группы есть **способ определить непустоту строки**, и это не деталь
реализации. Дефект позиций прожил незамеченным именно потому, что покрытие было
полным — 224 сессии из 224, — а значений в строках не было: 5 из 57 029. Отчёт,
считающий только покрытие, объявил бы группу собранной.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_ai.config import Settings
from financial_ai.market_data.models import (
    AssetSector,
    EquityAggregate,
    EquityDailyBar,
    FuturesPosition,
    GlobalDailySeries,
)


class GroupId(StrEnum):
    """Идентификатор группы. Строковый: он же приходит в запросе и уходит в отчёт."""

    QUOTES = "quotes"
    AGGREGATES = "aggregates"
    GLOBAL = "global"
    POSITIONS = "positions"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class SourceGroup:
    """Описание одной группы."""

    group_id: GroupId
    title: str

    # Идентификаторы источников, которые в неё входят. По ним группа
    # отображается на шаги сбора при выборочном догоне.
    source_ids: tuple[str, ...]

    # Таблица наблюдений группы и её столбец даты сессии. У справочников оси
    # сессий нет, и поле пустое.
    model: type
    session_column: str | None

    # Столбцы, хотя бы один из которых должен быть заполнен, чтобы строка
    # считалась непустой.
    value_columns: tuple[str, ...]

    # Столбец, по которому наблюдение относится к источнику, и начала имён,
    # принадлежащие каждому источнику. Заполняется только там, где в одну
    # таблицу пишут НЕСКОЛЬКО источников: иначе наблюдения группы общие, и
    # значение одного источника закрывало бы сессию для остальных (FR-047).
    key_column: str | None = None
    source_keys: dict[str, tuple[str, ...]] | None = None

    def keys_of(self, source_id: str) -> tuple[str, ...] | None:
        """Начала имён рядов, принадлежащих источнику. ``None`` — вся таблица."""
        if self.source_keys is None:
            return None
        return self.source_keys.get(source_id, ())

    @property
    def has_history(self) -> bool:
        """Есть ли у группы ось сессий.

        Справочник текущего состояния не бывает «недобранным»: у него нет окна,
        и ноль покрытия читался бы как «ничего не собрано».
        """
        return self.session_column is not None

    def window_sessions(self, settings: Settings) -> int | None:
        """Глубина окна группы в торговых сессиях."""
        if not self.has_history:
            return None
        if self.group_id is GroupId.POSITIONS:
            return settings.market_data_positions_window_sessions
        if self.group_id is GroupId.GLOBAL:
            return settings.market_data_global_window_sessions
        return settings.market_data_price_window_sessions


GROUPS: tuple[SourceGroup, ...] = (
    SourceGroup(
        group_id=GroupId.QUOTES,
        title="котировки",
        source_ids=("equity_d1",),
        model=EquityDailyBar,
        session_column="session_date",
        # Цена закрытия: бумага могла не торговаться, и тогда пусто всё, но
        # закрытие — то, без чего наблюдение бессмысленно.
        value_columns=("close",),
    ),
    SourceGroup(
        group_id=GroupId.AGGREGATES,
        title="агрегаты",
        source_ids=("equity_agg",),
        model=EquityAggregate,
        session_column="session_date",
        value_columns=("value", "num_trades", "waprice"),
    ),
    SourceGroup(
        group_id=GroupId.GLOBAL,
        title="глобальные ряды",
        source_ids=("global_series", "cbr", "brent", "index_constituents"),
        model=GlobalDailySeries,
        session_column="session_date",
        value_columns=("value",),
        # Четыре источника в одной таблице. Принадлежность ряда источнику
        # видна по имени, и другого признака у наблюдения нет: собственного
        # столбца источника таблица не держит, а заводить его значило бы
        # переписывать историю (FR-047).
        key_column="series_id",
        source_keys={
            "global_series": ("IMOEX", "RTSI", "RGBI", "RVI", "USD_ISS"),
            "cbr": ("CBR_",),
            "brent": ("BRENT_",),
            "index_constituents": ("IDX_WEIGHT_",),
        },
    ),
    SourceGroup(
        group_id=GroupId.POSITIONS,
        title="позиции по фьючерсам",
        source_ids=("futures_positions",),
        model=FuturesPosition,
        session_column="session_date",
        # Любое из четырёх: покрытие по сторонам бывает частичным по-настоящему,
        # и это не то же самое, что пустая строка.
        value_columns=("fiz_long", "fiz_short", "jur_long", "jur_short"),
    ),
    SourceGroup(
        group_id=GroupId.REFERENCE,
        title="справочники",
        source_ids=("equity_sectors", "equity_lot_sizes"),
        model=AssetSector,
        session_column=None,
        value_columns=("sector",),
    ),
)

BY_ID: dict[GroupId, SourceGroup] = {group.group_id: group for group in GROUPS}


class UnknownGroupError(ValueError):
    """Запрошена группа, которой нет."""


def resolve(raw: list[str] | None) -> tuple[SourceGroup, ...]:
    """Разобрать выбор групп. Пусто или ``None`` — все группы."""
    if not raw:
        return GROUPS

    resolved: list[SourceGroup] = []
    for name in raw:
        try:
            group_id = GroupId(name)
        except ValueError as error:
            known = ", ".join(g.group_id.value for g in GROUPS)
            raise UnknownGroupError(f"неизвестная группа {name!r}; известны: {known}") from error
        resolved.append(BY_ID[group_id])
    return tuple(resolved)


def required(settings: Settings) -> tuple[SourceGroup, ...]:
    """Группы, входящие в обязательный вход модели.

    Перечень — конфигурация, а не константа кода: состав входа определяет
    модель, и он меняется без нас. Живёт здесь, а не рядом с расчётом
    готовности, потому что спрашивают его двое — готовность и сбор, — а второе
    объявление одного факта однажды разойдётся с первым.
    """
    wanted = {name.strip() for name in settings.daily_ml_required_data_groups if name.strip()}
    return tuple(group for group in GROUPS if group.group_id.value in wanted)


def source_ids_for(groups: tuple[SourceGroup, ...]) -> frozenset[str]:
    """Идентификаторы источников выбранных групп."""
    return frozenset(source_id for group in groups for source_id in group.source_ids)
