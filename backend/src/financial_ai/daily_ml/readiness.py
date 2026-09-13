"""Готовность данных за дату решения.

**Инвариант фичи**: Daily ML не запускается на неполном обязательном окне ни
при каких условиях. Пропущенный прогон ранжирования — норма, пропущенные
входные данные — нет.

Проверка двухступенчата, и это не оптимизация ради оптимизации. Дешёвая ступень
смотрит на пропуски и незакрытые источники обязательных групп; она выполняется
на каждом тике планировщика. Дорогая ступень — фактическая сборка набора; она
выполняется только перед запуском прогона, и её слово окончательно: именно
набор уходит модели, а объявление его полноты входит в дайджест.

Строить набор на каждом тике незачем — это 314 сессий по сотням активов.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import groups
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import trading_calendar


@dataclass(frozen=True, slots=True)
class Readiness:
    """Готова ли дата и, если нет, чего не хватает."""

    asof_date: dt.date
    ready: bool
    reason: str | None = None
    missing_groups: list[str] = field(default_factory=list)


def required_groups(settings: Settings) -> tuple[groups.SourceGroup, ...]:
    """Группы, входящие в обязательный вход модели.

    Перечень объявлен в реестре групп: его спрашивают и готовность, и сбор, а
    второе объявление одного факта однажды разойдётся с первым. Дивиденды в
    умолчание не входят — они не являются входом модели и на стенде регулярно
    отказывают из-за ненастроенного брокера.
    """
    return groups.required(settings)


async def evaluate(session: AsyncSession, settings: Settings, asof_date: dt.date) -> Readiness:
    """Дешёвая проверка готовности: пропуски и незакрытые источники."""
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    if not await calendar.is_session(asof_date):
        return Readiness(asof_date, False, "дата не является торговой сессией")

    missing: list[str] = []

    for group in required_groups(settings):
        window_size = group.window_sessions(settings)
        if window_size is None:
            # Справочник текущего состояния: окна у него нет, и «недобранным»
            # он не бывает.
            continue

        window = await calendar.window(asof_date, window_size)

        # Окно короче требуемого — это тоже неполный вход, просто по другой
        # причине: истории ещё нет. Модель считает по окну, и укороченное окно
        # даёт ей не то, на чём она обучалась. В начале истории и на свежем
        # стенде это единственная защита от прогона по одной сессии.
        if len(window) < window_size:
            missing.append(group.group_id.value)
            continue

        for source_id in group.source_ids:
            collected = await repository.sessions_with_successful_run(window, source_id)
            if len(collected) < len(window):
                missing.append(group.group_id.value)
                break

    if missing:
        return Readiness(
            asof_date,
            False,
            "в обязательном окне есть несобранные сессии",
            sorted(set(missing)),
        )

    return Readiness(asof_date, True)


async def latest_ready(
    session: AsyncSession, settings: Settings, not_after: dt.date
) -> dt.date | None:
    """Последняя дата, за которую обязательный вход полон.

    Ищется от свежей к старой и не глубже окна догона: если готовой даты нет и
    там, отставание уже такое, что чинится оно данными, а не ранжированием.
    """
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    window = await calendar.window(not_after, settings.catchup_window_sessions)
    for day in reversed(window):
        if (await evaluate(session, settings, day)).ready:
            return day

    return None


def dataset_is_complete(incomplete: Iterable[Mapping[str, object]], settings: Settings) -> bool:
    """Окончательная проверка: объявление полноты собранного набора.

    Набор сообщает, какие сессии окна остались несобранными и по каким
    источникам. Если среди них есть источник обязательной группы — вход неполон,
    и запускать модель нельзя, как бы ни выглядела дешёвая проверка.
    """
    required_sources = {
        source_id for group in required_groups(settings) for source_id in group.source_ids
    }

    for row in incomplete:
        sources = row.get("sources")
        if not isinstance(sources, list):
            continue
        if required_sources & {str(source) for source in sources}:
            return False

    return True


async def is_stale(
    session: AsyncSession,
    settings: Settings,
    asof_date: dt.date,
    stored_digest: str,
    since: dt.datetime | None = None,
    window: tuple[dt.date | None, dt.date | None] | None = None,
    cheap_only: bool = False,
) -> bool:
    """Изменился ли вход с момента прогона.

    Признак **вычисляется**, а не хранится: закрытие одной исторической дыры
    меняет дайджест каждой даты, в чьё окно эта сессия входит — до 314 записей.
    Хранимый признак пришлось бы обновлять пакетом, и он расходился бы с фактом.

    Стоимость — пересборка набора: 314 сессий на 512 активов, секунды
    процессорного времени. Поэтому перед ней задаётся дешёвый вопрос.

    **Если после прогона ничего не собирали, вход измениться не мог.**
    Содержимое набора — функция от сохранённых данных, данные попадают в
    хранилище только через сбор, и каждый сбор записан в таблице исходов. Один
    индексный запрос заменяет семнадцать секунд. Дорогая ветка остаётся для
    случая, когда сбор всё-таки был: тогда дайджест сравнивается честно.

    Сбор — не единственный способ изменить набор: смена глубины окна в
    настройках меняет его состав, ничего не собирая. Поэтому дешёвая ветка
    требует ещё и совпадения окна: границы, записанные с прогоном, сверяются с
    теми, что дало бы окно сейчас.

    `since` — момент, после которого изменения имеют значение (окончание
    прогона); `window` — границы окна, записанные с прогоном. Без них дешёвый
    вопрос не задаётся и набор пересобирается, как раньше.

    `cheap_only` останавливается на дешёвой ветке: «не доказано, что вход тот
    же» возвращается как «устарел». Так спрашивает реконсиляция — ей нужен не
    точный ответ, а решение «можно ли пропустить пересборку».
    """
    from financial_ai.ranking import dataset as dataset_module

    if since is not None and window is not None and None not in window:
        market = MarketDataRepository(session)
        # Календарь исключён намеренно: он обновляется каждый день, но на окно
        # прошедшей даты влияет, только если новая сессия попала ВНУТРЬ окна —
        # а это видно по сдвигу его границ, который проверяется ниже.
        if not await market.collected_since(since, (trading_calendar.SOURCE_ID,)):
            sessions = await TradingCalendar(market).window(
                asof_date, settings.market_data_price_window_sessions
            )
            if sessions and (sessions[0], sessions[-1]) == window:
                return False

    if cheap_only:
        # Дешёвая ветка не доказала неизменность. Пересобирать здесь нельзя:
        # вызывающий спрашивал именно про дешёвый ответ.
        return True

    try:
        current = await dataset_module.build_dataset(session, settings, asof_date)
    except dataset_module.DatasetError:
        # Набор больше не собирается: сравнивать не с чем. Прогон от этого
        # устаревшим не становится — он остаётся фактом.
        return False

    return current.digest != stored_digest
