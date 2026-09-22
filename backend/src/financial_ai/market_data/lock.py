"""Межпроцессное владение прогоном сбора рыночных данных.

Рабочая сессия сбора коммитится после пачек.  Поэтому advisory-lock держится
отдельным соединением: транзакция рабочей сессии не может случайно освободить
владение, а закрытие процесса освобождает его силами PostgreSQL.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financial_ai.db.engine import get_session_factory

# Не совпадает с блокировками синхронизации счёта (1) и Daily ML (2).
LOCK_CLASS_ID = 817_241
LOCK_OBJECT_ID = 3


class MarketDataAlreadyRunningError(RuntimeError):
    """Другой процесс уже собирает рыночные данные."""


class MarketDataRunLock:
    """Session-level PostgreSQL lock, удерживаемый отдельной сессией."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._session: AsyncSession | None = None

    async def acquire(self) -> None:
        if self._session is not None:
            raise RuntimeError("владение сбором уже захвачено")

        session = self._session_factory()
        await session.__aenter__()
        try:
            result = await session.execute(
                text("select pg_try_advisory_lock(:classid, :objid)"),
                {"classid": LOCK_CLASS_ID, "objid": LOCK_OBJECT_ID},
            )
            if not bool(result.scalar()):
                raise MarketDataAlreadyRunningError("сбор рыночных данных уже выполняется")
        except BaseException:
            await session.__aexit__(None, None, None)
            raise
        self._session = session

    async def release(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            await session.execute(
                text("select pg_advisory_unlock(:classid, :objid)"),
                {"classid": LOCK_CLASS_ID, "objid": LOCK_OBJECT_ID},
            )
        finally:
            await session.__aexit__(None, None, None)
