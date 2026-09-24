"""Календарь спрашивается после порога, а не только на первом тике суток.

Дефект, ради которого написан этот тест, стоил суток задержки каждому дню:
гейт разрешал опрос, если дата последнего успешного прогона меньше сегодняшней.
Первый тик суток приходится на 00:0x, когда сегодняшних торгов ещё не было, а
повтор в тот же день запрещался. Сегодняшняя дата попадала в календарь только
следующей ночью — и сессия собиралась на сутки позже, а порог 19:30 не работал
вовсе (spec 008, FR-040, SC-010).
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.config import Settings
from financial_ai.market_data.advance import calendar_is_due
from financial_ai.market_data.calendar import MOSCOW

pytestmark = pytest.mark.asyncio

TODAY = dt.date(2026, 9, 17)


def moment(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(TODAY.year, TODAY.month, TODAY.day, hour, minute, tzinfo=MOSCOW)


class FakeRepository:
    """Хранилище-подделка: важна только отметка последнего успешного прогона."""

    def __init__(self, last: dt.datetime | None) -> None:
        self._last = last

    async def last_successful_run_at(self, source_id: str) -> dt.datetime | None:
        return self._last

    async def latest_trading_session(self, not_after: dt.date | None = None) -> dt.date | None:
        # Сегодняшняя сессия уже в календаре: повторный опрос после порога
        # нужен только пока её нет (FR-040, уточнение 2026-09-23).
        return not_after


async def test_без_единого_прогона_календарь_спрашивается() -> None:
    assert await calendar_is_due(FakeRepository(None), moment(9), Settings())


async def test_после_порога_календарь_спрашивается_повторно() -> None:
    """Ночной опрос не видел сегодняшних торгов — значит нужен ещё один."""
    ночью = moment(0, 5)

    assert await calendar_is_due(FakeRepository(ночью), moment(19, 31), Settings())


async def test_до_порога_второй_раз_не_спрашивается() -> None:
    """Пока сессия не закрылась, новых дат в календаре не появится."""
    ночью = moment(0, 5)

    assert not await calendar_is_due(FakeRepository(ночью), moment(15), Settings())


async def test_после_порога_дважды_не_спрашивается() -> None:
    вечером = moment(19, 40)

    assert not await calendar_is_due(FakeRepository(вечером), moment(21), Settings())


async def test_вчерашний_опрос_устарел() -> None:
    вчера = moment(20) - dt.timedelta(days=1)

    assert await calendar_is_due(FakeRepository(вчера), moment(1), Settings())


class CalendarWithout:
    """Календарь, в котором последняя сессия — ``latest``."""

    def __init__(self, last: dt.datetime, latest: dt.date) -> None:
        self._last = last
        self._latest = latest

    async def last_successful_run_at(self, source_id: str) -> dt.datetime | None:
        return self._last

    async def latest_trading_session(self, not_after: dt.date | None = None) -> dt.date | None:
        return self._latest


async def test_вчерашний_день_после_полуночи_переспрашивается() -> None:
    """Ночь на 24.09.2026: 23.09 опубликован после полуночи (FR-040b).

    Последний опрос — в 00:00:52, и 24-го правило ждало порога 19:30 ради
    нового дня: 23.09 в 08:37 был опубликован, но не собирался.
    """
    asked = dt.datetime(2026, 9, 24, 0, 0, 52, tzinfo=MOSCOW)
    repository = CalendarWithout(asked, dt.date(2026, 9, 22))

    assert not await calendar_is_due(
        repository, dt.datetime(2026, 9, 24, 0, 10, tzinfo=MOSCOW), Settings()
    )
    assert await calendar_is_due(
        repository, dt.datetime(2026, 9, 24, 8, 37, tzinfo=MOSCOW), Settings()
    )


async def test_подтверждённый_вчерашний_день_до_порога_не_переспрашивается() -> None:
    asked = dt.datetime(2026, 9, 24, 0, 30, tzinfo=MOSCOW)
    repository = CalendarWithout(asked, dt.date(2026, 9, 23))

    assert not await calendar_is_due(
        repository, dt.datetime(2026, 9, 24, 12, tzinfo=MOSCOW), Settings()
    )
