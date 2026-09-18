"""Календарь раздела: что известно про каждый день месяца.

Календарь показывает **только состоявшиеся торги**. Дней справа от сегодняшнего
в нём нет и быть не может: он строится по истории торгов опорной бумаги, а не по
справочнику праздников. Поэтому будущие дни интерфейс помечает ожиданием, а
сервер о них ничего не утверждает (spec 008, FR-023).

Состояние дня складывается из того же правила полноты, что и сводка: успех
одного источника группы не закрывает сессию за остальных.
"""

from __future__ import annotations

import calendar as pycalendar
import datetime as dt
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, groups
from financial_ai.market_data.calendar import moscow_today
from financial_ai.market_data.repository import MarketDataRepository

KIND_SESSION = "session"
KIND_NONTRADE = "nontrade"
KIND_OPEN = "open"
KIND_FUTURE = "future"


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """День месяца в календаре раздела."""

    date: dt.date
    kind: str
    # Состояние по каждой группе: collected | missing. Порядок групп — как в
    # сводке, чтобы точки под датой читались слева направо одинаково.
    groups: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {"date": self.date.isoformat(), "kind": self.kind, "groups": self.groups}


async def build_month(
    session: AsyncSession, settings: Settings, year: int, month: int
) -> dict[str, object]:
    """Состояние каждого дня месяца."""
    repository = MarketDataRepository(session)
    first = dt.date(year, month, 1)
    last = dt.date(year, month, pycalendar.monthrange(year, month)[1])
    today = moscow_today()

    sessions = await repository.sessions_between(first, last)
    known = set(sessions)

    # Пропуски считаются по тому же правилу, что и полнота сводки.
    missing_by_group: dict[str, set[dt.date]] = {}
    for group in groups.GROUPS:
        if not group.has_history:
            continue
        missing = await completeness.missing_sessions(repository, group, sessions)
        missing_by_group[group.group_id.value] = set(missing)

    days: list[CalendarDay] = []
    for offset in range((last - first).days + 1):
        day = first + dt.timedelta(days=offset)

        if day > today:
            kind = KIND_FUTURE
        elif day == today and day not in known:
            # Сегодня: торги могли быть, но подтверждения ещё нет. Календарь
            # узнаёт о дне по состоявшимся сделкам, а не заранее.
            kind = KIND_OPEN
        elif day in known:
            kind = KIND_SESSION
        else:
            kind = KIND_NONTRADE

        state = {
            group_id: ("missing" if day in missing else "collected")
            for group_id, missing in missing_by_group.items()
        }
        days.append(CalendarDay(day, kind, state if kind == KIND_SESSION else {}))

    # Граница листания назад. Дальше самой ранней известной сессии календарь
    # ничего не знает, и пустые месяцы там листались бы до 1970 года.
    earliest = await repository.earliest_trading_session()

    return {
        "month": f"{year:04d}-{month:02d}",
        "today": today.isoformat(),
        "earliest_month": None if earliest is None else f"{earliest:%Y-%m}",
        "days": [day.to_dict() for day in days],
    }
