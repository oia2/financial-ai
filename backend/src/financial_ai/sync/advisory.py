"""Межпроцессная блокировка синхронизации (FR-029).

``SingleFlight`` исключает параллельные обращения к брокеру внутри процесса.
Этого достаточно при одной реплике Worker; advisory lock PostgreSQL закрывает
случай нескольких реплик и перекрытия деплоев, когда два процесса иначе
пошли бы к брокеру одновременно.

Блокировка сеансовая: она держится, пока жива соединение-сессия, и снимается
явно либо при её закрытии.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Двухчастный ключ: в таком виде блокировку видно в pg_locks по classid/objid,
# что позволяет отвечать на вопрос «идёт ли синхронизация прямо сейчас».
LOCK_CLASS_ID = 1094795585  # 'FAI1'
LOCK_OBJECT_ID = 1


async def try_acquire(session: AsyncSession) -> bool:
    """Пытается взять блокировку, не дожидаясь освобождения."""
    result = await session.execute(
        text("select pg_try_advisory_lock(:classid, :objid)"),
        {"classid": LOCK_CLASS_ID, "objid": LOCK_OBJECT_ID},
    )
    return bool(result.scalar())


async def release(session: AsyncSession) -> None:
    await session.execute(
        text("select pg_advisory_unlock(:classid, :objid)"),
        {"classid": LOCK_CLASS_ID, "objid": LOCK_OBJECT_ID},
    )


async def is_held(session: AsyncSession) -> bool:
    """Держит ли кто-нибудь блокировку — то есть идёт ли синхронизация.

    Читается из ``pg_locks``, поэтому ответ одинаков для Backend-API и
    Backend-Worker, хотя блокировку берёт только второй.
    """
    result = await session.execute(
        text(
            "select exists("
            "  select 1 from pg_locks"
            "  where locktype = 'advisory' and classid = :classid"
            "    and objid = :objid and granted"
            ")"
        ),
        {"classid": LOCK_CLASS_ID, "objid": LOCK_OBJECT_ID},
    )
    return bool(result.scalar())
