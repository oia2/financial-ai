"""Полнота группы за сессии окна: один расчёт на готовность и на сбор.

Раньше их было два, и они расходились. Сводка «Рыночные данные» считала по
**наблюдениям** — строкам в таблице группы. Готовность ранжирования и поиск
недостающих сессий считали по **журналу прогонов** — записям «мы ходили за
данными в этот день». Для большинства источников ответы совпадали, и
расхождение не было видно.

Оно вылезло на диапазонных источниках. Ряды ЦБ, Brent, индекс и глобальные ряды
забираются **одним запросом за весь период**, а исход записывается на одну дату
— конец периода. Данные приходят за триста сессий, запись остаётся одна. Счёт по
журналу объявлял пустыми 299 сессий, данные за которые лежали в таблице рядом.

Последствия были такие: группа «глобальные ряды» не могла стать полной ни при
каком догоне, поэтому обязательным входом оставили одни котировки — иначе
ранжирование не запускалось бы вовсе.

**Правило здесь одно и объединяет оба признака.** Сессия закрыта, если за неё
есть непустое наблюдение ИЛИ успешный прогон источника. Первое чинит диапазонные
источники. Второе сохраняет законный случай «биржа ответила, данных за день
нет»: наблюдений не будет никогда, а сессия собрана.
"""

from __future__ import annotations

import datetime as dt

from financial_ai.config import Settings
from financial_ai.market_data.groups import SourceGroup
from financial_ai.market_data.repository import MarketDataRepository


async def missing_sessions(
    repository: MarketDataRepository,
    group: SourceGroup,
    window: list[dt.date],
) -> list[dt.date]:
    """Сессии окна, за которые группа не закрыта.

    Пустой список означает полноту. Группа без оси сессий полной считается
    всегда: у справочника текущего состояния окна нет, и «недобранным» он не
    бывает.
    """
    if not window or group.session_column is None:
        return []

    covered = await repository.sessions_with_observations(
        group.model, group.session_column, group.value_columns, window
    )

    # Пустой ответ биржи — законный исход, и наблюдений после него не будет.
    # Такие сессии закрывает журнал прогонов, поэтому он не отбрасывается, а
    # дополняет наблюдения.
    if len(covered) < len(window):
        for source_id in group.source_ids:
            covered |= await repository.sessions_with_successful_run(window, source_id)
            if len(covered) >= len(window):
                break

    return [day for day in window if day not in covered]


async def incomplete_groups(
    repository: MarketDataRepository,
    settings: Settings,
    groups_: tuple[SourceGroup, ...],
    windows: dict[str, list[dt.date]],
) -> dict[str, list[dt.date]]:
    """Незакрытые сессии по каждой группе, у которой они есть.

    Окно у каждой группы своё и приходит снаружи: позиции нужны модели на 82
    сессии, остальное на 314, и требовать позиции за давнюю сессию значило бы
    ходить за данными, которых нет и которые модели не нужны.
    """
    result: dict[str, list[dt.date]] = {}
    for group in groups_:
        window = windows.get(group.group_id.value, [])
        missing = await missing_sessions(repository, group, window)
        if missing:
            result[group.group_id.value] = missing
    return result
