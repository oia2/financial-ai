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
from financial_ai.market_data import groups
from financial_ai.market_data.calendar import TradingCalendar
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

    Правило одно на троих: сводку раздела, поиск пропусков и готовность
    ранжирования. Второе объявление того же правила однажды разошлось бы с
    первым — так уже было, и расхождение вылезло на диапазонных источниках.
    """
    if not window or group.session_column is None:
        return []

    observed = await repository.sessions_with_observations(
        group.model, group.session_column, group.value_columns, window
    )

    # **Полнота считается по КАЖДОМУ источнику группы, а не по любому.**
    # Прежняя версия объявляла сессию закрытой, если отработал хоть один
    # источник. Для группы из одного источника это верно; для «глобальных
    # рядов» их четыре — ряды ЦБ, Brent, индекс и ряды ISS, — и успех одного
    # ничего не говорит про остальные. Пропуск в Brent закрывался успехом ЦБ и
    # не становился работой (spec 008, FR-032).
    closed: set[dt.date] | None = None
    for source_id in group.source_ids:
        # Пустой ответ биржи — законный исход, и наблюдений после него не
        # будет: такие сессии закрывает журнал прогонов. Он не отбрасывается, а
        # дополняет наблюдения.
        by_source = observed | await repository.sessions_with_successful_run(window, source_id)
        closed = by_source if closed is None else (closed & by_source)
        if not closed:
            break

    return [day for day in window if day not in (closed or set())]


async def incomplete_sessions(
    repository: MarketDataRepository,
    calendar: TradingCalendar,
    settings: Settings,
    last_closed: dt.date,
    closed: list[dt.date] | None = None,
) -> list[dt.date]:
    """Закрытые сессии, за которые данные неполны, — по ВСЕМ группам.

    Одно правило на ежедневный сбор и на ручной. Прежде их было два: автосбор
    считал по всем группам, а ручной догон — только по котировкам, и сессия с
    собранными котировками и пустыми позициями в план не попадала. Ровно с этой
    жалобы фича и началась; для автосбора её закрыли, для ручного — нет, и на
    стенде 2026-09-18 ручной догон запросил четыре сессии там, где недобранных
    были сотни (FR-031, FR-032).

    Окно у каждой группы своё: позиции нужны модели на 82 сессии, остальное на
    314. Требовать позиции за сессию трёхсотдневной давности значило бы ходить
    на биржу за данными, которых там нет и которые модели не нужны.

    ``closed`` ограничивает ответ известными закрытыми сессиями. Без него
    берётся всё окно группы.
    """
    incomplete: set[dt.date] = set()
    closed_set = set(closed) if closed is not None else None

    for group in groups.GROUPS:
        depth = group.window_sessions(settings)
        if depth is None:
            continue

        window = await calendar.window(last_closed, depth)
        if closed_set is not None:
            window = [day for day in window if day in closed_set]
        if not window:
            continue

        incomplete.update(await missing_sessions(repository, group, window))

    return sorted(incomplete)
