"""Торговый календарь: единственный источник истины о том, была ли сессия.

От него зависит всё остальное:

- **когда опрашивать биржу** — в неторговый день данных не появится;
- **что такое «предыдущие 314 сессий»** — торговых, а не календарных дней;
- **что такое `t+1`** — следующая торговая сессия, а не завтрашний день;
- **где в данных настоящая дыра** — если сессия была, а котировки нет, это
  пропуск; если сессии не было, пропуска нет.

Календарь строится эмпирически: даты, в которые торговалась опорная ликвидная
бумага, и есть торговые сессии. Приём грубоватый, но отражает ФАКТИЧЕСКИЕ
торги — переносы рабочих дней и внеплановые остановки попадают в него сами,
без ручного справочника.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from financial_ai.market_data.repository import MarketDataRepository

MOSCOW = ZoneInfo("Europe/Moscow")


class TradingCalendar:
    """Ответы на вопросы о торговых сессиях."""

    def __init__(self, repository: MarketDataRepository) -> None:
        self._repository = repository

    async def is_session(self, day: dt.date) -> bool:
        """Была ли в этот день торговая сессия."""
        return await self._repository.is_trading_session(day)

    async def latest_session(self, not_after: dt.date | None = None) -> dt.date | None:
        """Последняя завершённая торговая сессия не позже указанной даты."""
        return await self._repository.latest_trading_session(not_after)

    async def window(self, asof: dt.date, sessions: int) -> list[dt.date]:
        """Окно из ``sessions`` торговых сессий, оканчивающееся на ``asof``.

        Возвращает столько сессий, сколько есть: на ранней истории окно
        короче запрошенного, и это не ошибка — модель дополняет его слева.
        """
        return await self._repository.previous_sessions(asof, sessions)

    async def next_session(self, after: dt.date) -> dt.date | None:
        """Следующая торговая сессия — та, в которую исполнится сделка."""
        return await self._repository.next_trading_session(after)


def moscow_today() -> dt.date:
    """Сегодняшняя дата по времени биржи.

    Календарь и время закрытия сессии — московские. Считать «сегодня» по UTC
    значит регулярно ошибаться на день по вечерам.
    """
    return dt.datetime.now(MOSCOW).date()


def moscow_now() -> dt.datetime:
    return dt.datetime.now(MOSCOW)
