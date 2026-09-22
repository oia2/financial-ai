"""Межпроцессное владение сбором рыночных данных."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from financial_ai.db.engine import get_session_factory
from financial_ai.market_data.lock import MarketDataAlreadyRunningError, MarketDataRunLock

pytestmark = pytest.mark.db


async def test_second_owner_is_rejected_until_first_releases(db_session: object) -> None:
    """Второй путь останавливается до HTTP, потому что lock уже недоступен."""
    first = MarketDataRunLock()
    second = MarketDataRunLock()
    await first.acquire()
    try:
        with pytest.raises(MarketDataAlreadyRunningError):
            await second.acquire()
    finally:
        await first.release()

    await second.acquire()
    await second.release()


async def test_working_session_commit_does_not_release_owner(db_session: object) -> None:
    """Пачечный commit работает отдельно от соединения-владельца."""
    owner = MarketDataRunLock()
    await owner.acquire()
    try:
        factory = get_session_factory()
        async with factory() as working_session:
            await working_session.execute(text("select 1"))
            await working_session.commit()

        contender = MarketDataRunLock()
        with pytest.raises(MarketDataAlreadyRunningError):
            await contender.acquire()
    finally:
        await owner.release()
