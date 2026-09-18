"""Ручной сбор истории по датам раньше связи (T059, FR-035, FR-017).

**Испытание той формы, которая была невидима.** Во всех прежних сценариях
состава связь засевалась за год до окна, и случай «связь появилась ПОЗЖЕ окна»
не проверялся ни разу. Именно он и сломался, когда связи стали открываться раз
на прогон, а запасное соответствие было удалено: догон прошлого переставал
собирать позиции вовсе — по всем бумагам и на каждой сессии.

Причина в том, что список серий отвечает про сегодня. Связь подтверждается с
той даты, когда её спросили, и никогда раньше; связи ежедневного прогона
начинаются сегодняшним днём. Поэтому даты раньше первого интервала
спрашиваются самым ранним известным семейством — интервал при этом
по-прежнему говорит «подтверждено с такой-то даты», а наблюдение записывает
то семейство, которым собрано.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from financial_ai.market_data import links
from financial_ai.market_data.repository import MarketDataRepository, PositionRow
from financial_ai.market_data.sources import positions
from tests.market_data.conftest import FakePositionsClient

from .instruments import RecordedIss, seed_assets

pytestmark = pytest.mark.db

PAST = dt.date(2026, 9, 10)
TODAY = dt.date(2026, 9, 17)


async def _links_from_today(repository: MarketDataRepository, db_session: object) -> None:
    """Связи, заведённые ежедневным прогоном: они начинаются сегодня."""
    await seed_assets(repository, TODAY, ["SBER", "GAZP"])
    await links.sync_links(repository, RecordedIss(), TODAY)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]


async def test_догон_прошлого_собирает_позиции_до_первой_связи(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await _links_from_today(repository, db_session)

    # Интервал накрывает только сегодня — это факт, и переписывать его нельзя.
    assert await repository.active_links_on(PAST) == {}
    assert await repository.active_links_on(TODAY) != {}

    await seed_assets(repository, PAST, ["SBER", "GAZP"])
    await db_session.commit()  # type: ignore[attr-defined]

    client = FakePositionsClient(contracts={})
    written = await positions.sync_positions(client, repository, PAST)  # type: ignore[arg-type]

    # Спрошены обе бумаги тем семейством, которым подтверждена связь.
    assert sorted(code for code, _ in client.calls) == ["GAZR_F", "SBRF_F"]
    assert written == 2


async def test_наблюдение_помнит_семейство_которым_собрано(db_session: object) -> None:
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await _links_from_today(repository, db_session)
    await seed_assets(repository, PAST, ["SBER"])
    await db_session.commit()  # type: ignore[attr-defined]

    client = FakePositionsClient(contracts={})
    await positions.sync_positions(client, repository, PAST)  # type: ignore[arg-type]
    await db_session.commit()  # type: ignore[attr-defined]

    rows = await repository.positions_for_window([PAST])
    collected = {row.asset_id: row.contract_code for row in rows}
    assert collected["EQ_AST_SBER"] == "SBRF_F"

    # А интервал по-прежнему утверждает только то, что подтвердил источник.
    (interval,) = await repository.link_history("EQ_AST_SBER")
    assert interval.valid_from == TODAY


async def test_закрытая_связь_в_прошлое_не_воскресает(db_session: object) -> None:
    """Инструмента не стало — и в прошлое это правило не переносится.

    Закрытый интервал означает, что контракта больше нет. Подставлять его как
    «самую раннюю известную связь» значило бы спрашивать позиции по тому, чего
    на рынке нет.
    """
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await _links_from_today(repository, db_session)

    await repository.close_link("EQ_AST_GAZP", TODAY)
    await seed_assets(repository, PAST, ["SBER", "GAZP"])
    await db_session.commit()  # type: ignore[attr-defined]

    later = TODAY + dt.timedelta(days=1)
    await seed_assets(repository, later, ["SBER", "GAZP"])
    await repository.upsert_positions(
        [
            PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=PAST,
                contract_code="SBRF_F",
                fiz_long=Decimal("1"),
                fiz_short=None,
                jur_long=None,
                jur_short=None,
            )
        ]
    )
    await db_session.commit()  # type: ignore[attr-defined]

    client = FakePositionsClient(contracts={})
    await positions.sync_positions(client, repository, later)  # type: ignore[arg-type]

    assert [code for code, _ in client.calls] == ["SBRF_F"]
