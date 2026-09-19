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
        by_source: dict[str, set[dt.date]] | None = None,
        stopped: dict[str, set[dt.date]] | None = None,
    ) -> None:
        self._observed = observed or set()
        self._runs = runs or {}
        # Наблюдения, разложенные по источнику, — так их и спрашивает правило.
        # Ключ здесь — набор начал имён рядов, которым источник владеет.
        self._by_source = by_source or {}
        # Сессии, у которых последний исход источника — «прервано».
        self._stopped = stopped or {}

    async def sessions_with_observations(
        self,
        model: type,
        session_column: str,
        value_columns: tuple[str, ...],
        window: list[dt.date],
        key_column: str | None = None,
        keys: tuple[str, ...] | None = None,
    ) -> set[dt.date]:
        if key_column is not None and keys is not None:
            observed: set[dt.date] = set()
            for key in keys:
                observed |= self._by_source.get(key, set())
            return {day for day in window if day in observed}
        return {day for day in window if day in self._observed}

    async def sessions_with_successful_run(
        self, sessions: list[dt.date], source_id: str
    ) -> set[dt.date]:
        return {day for day in sessions if day in self._runs.get(source_id, set())}

    async def sessions_left_unfinished(
        self, sessions: list[dt.date], source_id: str
    ) -> set[dt.date]:
        return {day for day in sessions if day in self._stopped.get(source_id, set())}


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
    everywhere = {
        key: set(WINDOW)
        for source_id in group(groups.GroupId.GLOBAL).source_ids
        for key in (group(groups.GroupId.GLOBAL).keys_of(source_id) or ())
    }
    repository = FakeRepository(by_source=everywhere)

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == []


async def test_наблюдение_одного_источника_не_закрывает_сессию_остальным() -> None:
    """Обратная форма того же правила, и ровно ею дефект и жил.

    Четыре источника «глобальных рядов» пишут в ОДНУ таблицу, а наблюдения
    брались по всей таблице: строка ЦБ за сессию закрывала её сразу и Brent, и
    индексу, и рядам ISS. Правило «по каждому источнику» при этом формально
    выполнялось — оно просто ничего не проверяло (FR-047).
    """
    repository = FakeRepository(by_source={"CBR_": set(WINDOW)})

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == WINDOW


async def test_наблюдения_считаются_только_своими_рядами() -> None:
    """Собрано всё, кроме Brent за средний день, — и именно он остаётся работой."""
    by_source = {
        "IMOEX": set(WINDOW),
        "RTSI": set(WINDOW),
        "RGBI": set(WINDOW),
        "RVI": set(WINDOW),
        "USD_ISS": set(WINDOW),
        "CBR_": set(WINDOW),
        "IDX_WEIGHT_": set(WINDOW),
        "BRENT_": {WINDOW[0], WINDOW[2]},
    }
    repository = FakeRepository(by_source=by_source)

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.GLOBAL), WINDOW)

    assert missing == [WINDOW[1]]


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


async def test_прерванный_исход_перевешивает_наблюдение() -> None:
    """Остановка успевает записать собранное, и строка закрывала бы сессию.

    Отдельного исхода мало: полнота считается ещё и по наличию непустого
    наблюдения, а прерванный сбор успевает его оставить — остановка после
    первого инструмента из ста двадцати объявляла день собранным навсегда
    (FR-050).
    """
    repository = FakeRepository(
        runs={"equity_d1": set(WINDOW)},
        observed=set(WINDOW),
        stopped={"equity_d1": {WINDOW[1]}},
    )

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.QUOTES), WINDOW)

    assert missing == [WINDOW[1]]


async def test_добранная_после_остановки_сессия_незакрытой_не_остаётся() -> None:
    """Обратная форма: правило смотрит на ПОСЛЕДНИЙ исход, а не на любой бывший."""
    repository = FakeRepository(runs={"equity_d1": set(WINDOW)}, observed=set(WINDOW))

    missing = await completeness.missing_sessions(repository, group(groups.GroupId.QUOTES), WINDOW)

    assert missing == []
