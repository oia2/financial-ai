"""Связи инструментов синхронизируются раз на прогон (T053).

Список серий срочного рынка и открытый интерес описывают **сегодняшний** состав
рынка. Спрашивать их на каждую сессию догона — три обращения к бирже за ответом,
который не изменится, помноженные на длину дыры: на трёхсотсессионном догоне это
девятьсот запросов ни за чем.

Ровно то же правило действует для диапазонных источников, и по той же причине.
Испытание держит его числом, а не намерением.
"""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository

from .instruments import RecordedIss, seed_assets

pytestmark = pytest.mark.db

SESSIONS = [dt.date(2026, 9, 14), dt.date(2026, 9, 15), dt.date(2026, 9, 16)]


async def test_состав_спрашивается_один_раз_на_прогон(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    for day in SESSIONS:
        await seed_assets(repository, day, ["SBER", "GAZP"])
    await db_session.commit()  # type: ignore[attr-defined]

    iss = RecordedIss()
    await links.sync_links(repository, iss, SESSIONS[0])  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    # Проверка эмитента — только у новых связей: цена сверки пропорциональна
    # изменению состава, а не размеру доски.
    first_pass = len(iss.description_calls)
    assert first_pass > 0

    await links.sync_links(repository, iss, SESSIONS[-1])  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    # Второй прогон по тому же составу не стоит ни одного описания.
    assert len(iss.description_calls) == first_pass


async def test_интервал_накрывает_всё_окно_прогона(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    for day in SESSIONS:
        await seed_assets(repository, day, ["SBER"])
    await db_session.commit()  # type: ignore[attr-defined]

    # Датируется началом окна — тем же днём, каким датировал интервал прежний
    # посессионный вызов: первая сессия открывала связь, остальные продлевали.
    await links.sync_links(repository, RecordedIss(), SESSIONS[0])  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    for day in SESSIONS:
        assert await repository.active_links_on(day) == {"EQ_AST_SBER": "SBRF_F"}


async def test_пустой_список_серий_не_закрывает_связи(db_session: object) -> None:
    """Молчание источника — не исчезновение рынка.

    Закрыть связи по пустому ответу значило бы стереть соответствие по всему
    рынку из-за одного неудачного обращения.
    """
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await seed_assets(repository, SESSIONS[0], ["SBER"])
    await links.sync_links(repository, RecordedIss(), SESSIONS[0])  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    events = await links.sync_links(  # type: ignore[arg-type]
        repository, RecordedIss(series=[], open_interest={}), SESSIONS[-1]
    )
    await db_session.commit()  # type: ignore[attr-defined]

    assert events == []
    assert await repository.active_links_on(SESSIONS[-1]) == {"EQ_AST_SBER": "SBRF_F"}
