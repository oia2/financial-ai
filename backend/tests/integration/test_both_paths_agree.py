"""Оба пути сбора отвечают на вопросы одинаково.

**Испытание против одной и той же ошибки, повторённой трижды.** Автоматический
сбор и ручной догон — два пути к одному делу, и каждый раз, когда правило
менялось в одном, второй оставался на старом: сначала поиск пропусков считался
по всем группам в автосборе и по котировкам в ручном, потом остановка
проверялась между обращениями в ручном и между сессиями в автоматическом.
Оба раза расхождение находил владелец проекта на стенде, а не тесты.

Здесь оба пути проверяются рядом: если правило поменяют в одном, тест упадёт.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from financial_ai.config import Settings
from financial_ai.market_data import advance, completeness, ingest
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

SESSIONS = [dt.date(2026, 9, 14), dt.date(2026, 9, 15), dt.date(2026, 9, 16)]


async def _seed_quotes_only(repository: MarketDataRepository) -> None:
    """Сессии, у которых есть котировки и больше ничего."""
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", SESSIONS[-1])
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", SESSIONS[-1])
    await repository.upsert_daily_bars(
        [
            DailyBar(
                asset_id="EQ_AST_SBER",
                price_series_id="EQ_PRS_SBER",
                session_date=day,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
            for day in SESSIONS
        ]
    )


async def test_сессия_с_одними_котировками_недобрана_для_обоих(db_session: object) -> None:
    """Правило полноты одно, и оба пути видят одно и то же."""
    repository = MarketDataRepository(db_session)  # type: ignore[arg-type]
    await _seed_quotes_only(repository)
    await db_session.commit()  # type: ignore[attr-defined]

    calendar = TradingCalendar(repository)
    missing = await completeness.incomplete_sessions(
        repository, calendar, Settings(), SESSIONS[-1], closed=SESSIONS
    )

    # Наличие баров сессию не закрывает: у неё нет ни агрегатов, ни глобальных
    # рядов, ни позиций.
    assert missing == SESSIONS


def test_остановку_принимают_оба_пути() -> None:
    """У обоих входов в сбор есть признак остановки.

    Проверка формы, а не поведения: поведение проверено в своих испытаниях, а
    здесь ловится случай «добавили параметр в один путь и забыли про второй».
    """
    import inspect

    assert "should_stop" in inspect.signature(ingest.ingest_session).parameters
    assert "should_stop" in inspect.signature(ingest.catch_up).parameters
    assert "should_stop" in inspect.signature(advance.advance).parameters
