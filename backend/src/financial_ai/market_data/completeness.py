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

**Строки сами по себе не закрывают работу.** Источник закрывает дату только
доказательствами всех обязательных единиц работы действующей версии правила
полноты (FR-032f). Частичные
строки остаются полезными данными, но не заменяют обработку всего применимого
набора рядов или пар. Успешная проверка может законно завершиться без новых
строк; старые неподтверждённые даты требуют аудита и не запускают автодогон.
"""

from __future__ import annotations

import datetime as dt

from financial_ai.config import Settings
from financial_ai.market_data import groups
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.groups import SourceGroup
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.verification import required_work_keys


async def source_windows(
    repository: MarketDataRepository,
    settings: Settings,
    asof: dt.date,
) -> dict[str, frozenset[dt.date]]:
    """Собственное окно каждого посессионного источника на дату ``asof``.

    План — это пары «источник — дата», а не общий список дат для всех
    (FR-033c). При выборе всех групп общий список дат доходил до позиций
    целиком: 232 даты вне их окна в 82 сессии, около 15,8 тысячи лишних
    обращений (анализ 2026-09-23, A2).
    """
    calendar = TradingCalendar(repository)
    by_depth: dict[int, frozenset[dt.date]] = {}
    windows: dict[str, frozenset[dt.date]] = {}
    for group in groups.GROUPS:
        depth = group.window_sessions(settings)
        if depth is None:
            continue
        if depth not in by_depth:
            by_depth[depth] = frozenset(await calendar.window(asof, depth))
        own = frozenset(group.trim(sorted(by_depth[depth])))
        for source_id in group.source_ids:
            # Объединение, а не последняя группа: у котировок две группы —
            # акций и фондов, — и окно фондов короче. Присваивание урезало бы
            # сбор котировок до 22.06.2026 (FR-060b).
            windows[source_id] = windows.get(source_id, frozenset()) | own
    return windows


async def closed_sources_for(repository: MarketDataRepository, session_date: dt.date) -> set[str]:
    """Сбор пропускает ровно те источники, которые сводка считает закрытыми."""
    closed: set[str] = set()
    for group in groups.GROUPS:
        if not group.has_history:
            continue
        for source_id, days in (await closed_by_source(repository, group, [session_date])).items():
            if session_date in days:
                closed.add(source_id)
    return closed


async def closed_by_source(
    repository: MarketDataRepository,
    group: SourceGroup,
    window: list[dt.date],
) -> dict[str, set[dt.date]]:
    """Закрытые сессии по каждому источнику группы — за один проход.

    Счёт нужен дважды: сводке группы и строке источника под ней. Считать его
    дважды — втрое больше запросов на каждую отрисовку сводки, а её опрашивают
    раз в три секунды: на стенде 2026-09-19 воркер выбрал весь пул соединений
    за две минуты (FR-032).
    """
    return {
        source_id: await closed_sessions(repository, group, source_id, window)
        for source_id in group.source_ids
    }


async def missing_sessions(
    repository: MarketDataRepository,
    group: SourceGroup,
    window: list[dt.date],
    closed_sources: dict[str, set[dt.date]] | None = None,
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

    # **Полнота считается по КАЖДОМУ источнику группы, а не по любому.**
    # Прежняя версия объявляла сессию закрытой, если отработал хоть один
    # источник. Для группы из одного источника это верно; для «глобальных
    # рядов» их четыре — ряды ЦБ, Brent, индекс и ряды ISS, — и успех одного
    # ничего не говорит про остальные. Пропуск в Brent закрывался успехом ЦБ и
    # не становился работой (spec 008, FR-032).
    #
    # **И наблюдения берутся ТОЖЕ по источнику.** Пока они брались по всей
    # таблице, правило выше не работало вовсе: у «глобальных рядов» одна
    # таблица на четыре источника, и строка ЦБ за сессию закрывала её сразу
    # всем — достаточно было одного ряда, чтобы день считался собранным
    # (FR-047).
    by_source_all = closed_sources or await closed_by_source(repository, group, window)

    closed: set[dt.date] | None = None
    for source_id in group.source_ids:
        by_source = by_source_all.get(source_id, set())
        closed = by_source if closed is None else (closed & by_source)
        if not closed:
            break

    return [day for day in window if day not in (closed or set())]


async def closed_sessions(
    repository: MarketDataRepository,
    group: SourceGroup,
    source_id: str,
    window: list[dt.date],
) -> set[dt.date]:
    """Сессии окна, закрытые ОДНИМ источником группы.

    Одно правило на счёт группы и на счёт источника под ней. Пока их было два
    — группа считала по наблюдениям и журналу, а строка источника только по
    журналу, — сводка показывала 71 сессию у группы и 70 у её единственного
    источника. Два числа об одном и том же расходятся всегда, вопрос только
    когда это заметят (FR-032).
    """
    if group.session_column is None or not window:
        return set()

    # Закрывают доказательства ВСЕХ обязательных единиц работы действующей
    # версии, а не статус прогона (FR-032f). Наличие строки не доказывает, что
    # источник обработал все свои ряды или все применимые пары (FR-032).
    #
    # Статус прогона — свёртка по всему его периоду, и прежнее правило
    # «успех и доказательства и нет более поздней неудачи» теряло доказанное:
    # диапазон, упавший на одной дате, не закрывал остальные доказанные, а
    # поздняя неудачная попытка за уже доказанную дату снова открывала её —
    # так Brent оставался «с ошибкой» при собранных значениях (анализ
    # 2026-09-23, A4).
    return await repository.sessions_with_all_work(source_id, window, required_work_keys(source_id))


async def requires_audit_sessions(
    repository: MarketDataRepository,
    group: SourceGroup,
    source_id: str,
    window: list[dt.date],
    *,
    closed: set[dt.date] | None = None,
    boundary: dt.date | None = None,
) -> set[dt.date]:
    """Старая область без успешного подтверждения текущим правилом."""
    if not window or group.session_column is None:
        return set()
    if boundary is None:
        boundary = await repository.coverage_boundary()
    if boundary is None:
        return set()
    old_window = [day for day in window if day <= boundary]
    if not old_window:
        return set()
    source_closed = closed
    if source_closed is None:
        source_closed = await closed_sessions(repository, group, source_id, old_window)
    return set(old_window) - source_closed


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

        window = group.trim(await calendar.window(last_closed, depth))
        if closed_set is not None:
            window = [day for day in window if day in closed_set]
        if not window:
            continue

        source_closures = await closed_by_source(repository, group, window)
        missing = await missing_sessions(repository, group, window, source_closures)
        # Старый непроверенный диапазон показывается как audit-required, но
        # не превращается в автоматический исторический догон после миграции.
        requires_audit: set[dt.date] = set()
        boundary = await repository.coverage_boundary()
        for source_id in group.source_ids:
            requires_audit.update(
                await requires_audit_sessions(
                    repository,
                    group,
                    source_id,
                    window,
                    closed=source_closures[source_id],
                    boundary=boundary,
                )
            )
        missing = [day for day in missing if day not in requires_audit]
        incomplete.update(missing)

    return sorted(incomplete)
