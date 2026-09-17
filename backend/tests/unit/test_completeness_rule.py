"""Правило полноты: по каждому источнику группы и с учётом периода исхода.

Два дефекта, ради которых эти тесты написаны, уже случались в проекте:

1. полнота группы закрывалась успехом ЛЮБОГО её источника, поэтому пропуск в
   Brent закрывался успехом ЦБ и не становился работой;
2. источник с выборкой за период записывает исход на конец периода, и счёт по
   дате объявлял пустыми все сессии диапазона, кроме последней, — данные за них
   при этом лежали в таблице рядом.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data import completeness, groups

pytestmark = pytest.mark.asyncio

WINDOW = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]


class FakeRepository:
    """Хранилище-подделка: наблюдения и успешные исходы задаются напрямую."""

    def __init__(
        self,
        observed: set[dt.date] | None = None,
        runs: dict[str, set[dt.date]] | None = None,
    ) -> None:
        self._observed = observed or set()
        self._runs = runs or {}

    async def sessions_with_observations(
        self,
        model: type,
        session_column: str,
        value_columns: tuple[str, ...],
        window: list[dt.date],
    ) -> set[dt.date]:
        return {day for day in window if day in self._observed}

    async def sessions_with_successful_run(
        self, sessions: list[dt.date], source_id: str
    ) -> set[dt.date]:
        return {day for day in sessions if day in self._runs.get(source_id, set())}


def group(group_id: groups.GroupId) -> groups.SourceGroup:
    return groups.BY_ID[group_id]


async def test_успех_одного_источника_не_закрывает_группу_за_остальных() -> None:
    """У «глобальных рядов» четыре источника: успех одного ничего не говорит о трёх других."""
    repository = FakeRepository(runs={"cbr": set(WINDOW)})

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == WINDOW


async def test_группа_закрыта_когда_отработал_каждый_источник() -> None:
    every_source = {source_id: set(WINDOW) for source_id in group(groups.GroupId.GLOBAL).source_ids}
    repository = FakeRepository(runs=every_source)

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == []


async def test_непустое_наблюдение_закрывает_сессию_без_журнала() -> None:
    """Наблюдение — тоже доказательство: диапазонный источник пишет исход на конец периода."""
    repository = FakeRepository(observed=set(WINDOW))

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == []


async def test_дыра_у_одного_источника_остаётся_работой() -> None:
    runs = {source_id: set(WINDOW) for source_id in group(groups.GroupId.GLOBAL).source_ids}
    runs["brent"] = {WINDOW[0], WINDOW[2]}
    repository = FakeRepository(runs=runs)

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == [WINDOW[1]]


async def test_группа_из_одного_источника_считается_как_прежде() -> None:
    repository = FakeRepository(runs={"equity_d1": {WINDOW[0], WINDOW[1]}})

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.QUOTES), WINDOW)

    assert missing == [WINDOW[2]]


async def test_справочник_без_оси_сессий_недобранным_не_бывает() -> None:
    repository = FakeRepository()

    missing = await completeness.missing_sessions(
        repository, group(groups.GroupId.REFERENCE), WINDOW
    )

    assert missing == []
