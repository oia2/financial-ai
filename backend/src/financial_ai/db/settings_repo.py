"""Настройка интервала автообновления (FR-031, FR-034).

Значение хранится в БД и является единственным источником истины: планировщик
перечитывает его в начале каждого цикла, поэтому изменение применяется без
перезапуска (SC-012).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.db.models import (
    INTERVAL_DEFAULT_SECONDS,
    INTERVAL_MAX_SECONDS,
    INTERVAL_MIN_SECONDS,
    SINGLETON_ID,
    AccountRefreshSettings,
)


class IntervalOutOfRangeError(ValueError):
    """Интервал вне допустимого диапазона."""


@dataclass(frozen=True, slots=True)
class RefreshInterval:
    """Действующий интервал вместе с границами — их показывает интерфейс."""

    interval_seconds: int
    min_seconds: int = INTERVAL_MIN_SECONDS
    max_seconds: int = INTERVAL_MAX_SECONDS
    default_seconds: int = INTERVAL_DEFAULT_SECONDS


async def _get_or_create(session: AsyncSession) -> AccountRefreshSettings:
    settings = await session.get(AccountRefreshSettings, SINGLETON_ID)
    if settings is None:
        settings = AccountRefreshSettings(
            id=SINGLETON_ID, interval_seconds=INTERVAL_DEFAULT_SECONDS
        )
        session.add(settings)
        await session.flush()
    return settings


async def get_interval_seconds(session: AsyncSession) -> int:
    settings = await _get_or_create(session)
    return settings.interval_seconds


async def get_interval(session: AsyncSession) -> RefreshInterval:
    return RefreshInterval(interval_seconds=await get_interval_seconds(session))


async def set_interval_seconds(session: AsyncSession, value: int) -> RefreshInterval:
    """Сохраняет новый интервал.

    Диапазон проверяется здесь, в Pydantic-схеме и CHECK-ограничением БД:
    недопустимое значение отклоняется на каждом уровне (US2 AS4).
    """
    if value < INTERVAL_MIN_SECONDS or value > INTERVAL_MAX_SECONDS:
        raise IntervalOutOfRangeError(
            f"интервал должен быть от {INTERVAL_MIN_SECONDS} до {INTERVAL_MAX_SECONDS} секунд"
        )

    settings = await _get_or_create(session)
    settings.interval_seconds = value
    await session.flush()
    return RefreshInterval(interval_seconds=value)
