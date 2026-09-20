"""Собранные позиции переживают выход по ошибке, остановку и обрыв процесса.

Источник позиций идёт по обращению НА ИНСТРУМЕНТ: десятки обращений на одну
сессию, минуты работы. Пока всё собранное копилось в памяти и записывалось
одним вызовом в самом конце, ошибка на восьмидесятом контракте уносила
семьдесят девять уже полученных ответов, а исход записывал ``rows_written = 0``
— то есть выглядел как «спросили и ничего не нашли» (T207, FR-032a, FR-050).

Проверяется именно граница **commit**, а не вызов ``upsert``: незакоммиченная
пачка живёт в памяти сессии и исчезает вместе с процессом ВМЕСТЕ со всеми
предыдущими пачками той же транзакции.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.interrupt import SourcePartialError, SourceStoppedError
from financial_ai.market_data.models import FuturesPosition
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import positions
from financial_ai.market_data.sources.positions_client import (
    PositionSnapshot,
    PositionsSourceError,
)
from tests.market_data.test_positions_source import SESSION, _seed_assets

pytestmark = pytest.mark.db

# Бумаги обходятся по алфавиту: GAZP раньше SBER. Падает ВТОРОЙ контракт —
# иначе до первого успешного ответа дело не доходит и проверять нечего.
CONTRACTS = {"SBER": "SBRF_F", "GAZP": "GAZR_F"}


class FailingAfter:
    """Источник, отвечающий по первым контрактам и падающий на заданном."""

    def __init__(self, fail_on: str, error: BaseException) -> None:
        self.fail_on = fail_on
        self.error = error
        self.calls: list[tuple[str, dt.date]] = []

    async def fetch(self, contract_code: str, day: dt.date) -> PositionSnapshot | None:
        self.calls.append((contract_code, day))
        if contract_code == self.fail_on:
            raise self.error
        return PositionSnapshot(
            trade_date=day,
            fiz_long=Decimal("100"),
            fiz_short=Decimal("50"),
            jur_long=Decimal("300"),
            jur_short=Decimal("350"),
        )


async def _stored(session: AsyncSession) -> dict[str, str]:
    rows = list(await session.scalars(select(FuturesPosition)))
    return {row.asset_id: row.contract_code for row in rows}


async def test_http_error_keeps_contracts_collected_before_it(
    db_session: AsyncSession,
) -> None:
    """Первый контракт успешен, второй упал — первый остаётся в БД."""
    await _seed_assets(db_session, ["SBER", "GAZP"], CONTRACTS)
    client = FailingAfter("SBRF_F", PositionsSourceError("источник недоступен"))

    with pytest.raises(SourcePartialError) as caught:
        await positions.sync_positions(
            client,  # type: ignore[arg-type]
            MarketDataRepository(db_session),
            SESSION,
        )

    assert caught.value.rows_written == 1
    assert caught.value.unfinished == ("SBRF_F/" + SESSION.isoformat(),)
    assert await _stored(db_session) == {"EQ_AST_GAZP": "GAZR_F"}


async def test_stop_keeps_contracts_collected_before_it(db_session: AsyncSession) -> None:
    """Остановка сохраняет собранное так же, как и ошибка."""
    await _seed_assets(db_session, ["SBER", "GAZP"], CONTRACTS)
    client = FailingAfter("SBRF_F", SourceStoppedError(detail="остановлено"))

    with pytest.raises(SourceStoppedError) as caught:
        await positions.sync_positions(
            client,  # type: ignore[arg-type]
            MarketDataRepository(db_session),
            SESSION,
        )

    assert caught.value.rows_written == 1
    assert await _stored(db_session) == {"EQ_AST_GAZP": "GAZR_F"}


async def test_saved_batch_survives_a_cold_restart(db_session: AsyncSession) -> None:
    """Холодный перезапуск после сохранённой пачки её не теряет.

    Откат транзакции изображает обрыв процесса: всё, что не закреплено, с ним
    исчезает. Закреплённое обязано пережить.
    """
    await _seed_assets(db_session, ["SBER", "GAZP"], CONTRACTS)
    client = FailingAfter("SBRF_F", PositionsSourceError("обрыв соединения"))

    with pytest.raises(SourcePartialError):
        await positions.sync_positions(
            client,  # type: ignore[arg-type]
            MarketDataRepository(db_session),
            SESSION,
        )

    await db_session.rollback()

    assert await _stored(db_session) == {"EQ_AST_GAZP": "GAZR_F"}


async def test_retry_asks_only_the_missing_pair(db_session: AsyncSession) -> None:
    """Повтор запрашивает только недобранное, а собранное не переспрашивает."""
    await _seed_assets(db_session, ["SBER", "GAZP"], CONTRACTS)
    repository = MarketDataRepository(db_session)

    first = FailingAfter("SBRF_F", PositionsSourceError("источник недоступен"))
    with pytest.raises(SourcePartialError):
        await positions.sync_positions(first, repository, SESSION)  # type: ignore[arg-type]

    second = FailingAfter("НЕТ_ТАКОГО", RuntimeError("не должно случиться"))
    written = await positions.sync_positions(second, repository, SESSION)  # type: ignore[arg-type]

    assert second.calls == [("SBRF_F", SESSION)]
    assert written == 1


async def test_other_family_does_not_prove_the_current_one_collected(
    db_session: AsyncSession,
) -> None:
    """Смена семейства не скрывает недобор и не затирает прежний контракт.

    Пока ключом собранного был один ``asset_id``, строка, собранная ДРУГИМ
    семейством, считалась собранной и для текущего: после смены семейства пара
    пропускалась, а ряд выглядел непрерывным, будучи склеенным из двух разных
    инструментов (FR-039).
    """
    await _seed_assets(db_session, ["SBER"], {"SBER": "SBRF_F"})
    repository = MarketDataRepository(db_session)
    await positions.sync_positions(
        FailingAfter("НЕТ_ТАКОГО", RuntimeError()),  # type: ignore[arg-type]
        repository,
        SESSION,
    )
    await db_session.commit()

    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRM_F",
        valid_from=SESSION,
        chosen_by="underlying_and_emitter",
    )
    await db_session.commit()

    client = FailingAfter("НЕТ_ТАКОГО", RuntimeError())
    written = await positions.sync_positions(client, repository, SESSION)  # type: ignore[arg-type]

    assert client.calls == [("SBRM_F", SESSION)]
    assert written == 1
    stored = list(await db_session.scalars(select(FuturesPosition)))
    assert sorted(row.contract_code for row in stored) == ["SBRF_F", "SBRM_F"]


async def test_empty_answer_does_not_erase_a_collected_value(
    db_session: AsyncSession,
) -> None:
    """Пустое поле повторного ответа не затирает уже полученное значение."""
    await _seed_assets(db_session, ["SBER"], {"SBER": "SBRF_F"})
    repository = MarketDataRepository(db_session)
    await repository.upsert_positions(
        [
            positions.PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=SESSION,
                contract_code="SBRF_F",
                fiz_long=Decimal("100"),
                fiz_short=Decimal("50"),
                jur_long=Decimal("300"),
                jur_short=Decimal("350"),
            )
        ]
    )
    await db_session.commit()

    # Повтор принёс три поля из четырёх: четвёртое неизвестно, а не обнулилось.
    await repository.upsert_positions(
        [
            positions.PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=SESSION,
                contract_code="SBRF_F",
                fiz_long=Decimal("111"),
                fiz_short=None,
                jur_long=Decimal("333"),
                jur_short=Decimal("377"),
            )
        ]
    )
    await db_session.commit()

    row = (await db_session.scalars(select(FuturesPosition))).one()
    assert row.fiz_long == Decimal("111")
    assert row.jur_long == Decimal("333")
    # Прежнее значение сохранено, нулём не подменено.
    assert row.fiz_short == Decimal("50")
