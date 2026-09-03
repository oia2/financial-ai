"""Тесты планировщика сбора.

Проверяется решение «пора или не пора», а не сам сбор: собственно сбор покрыт
в test_ingest_cycle.
"""

from __future__ import annotations

import datetime as dt

from financial_ai.config import Settings
from financial_ai.market_data.scheduler import MarketDataScheduler

MOSCOW_DAY = dt.date(2026, 8, 28)


def _scheduler(after_close: str = "19:30") -> MarketDataScheduler:
    settings = Settings(market_data_ingest_after_close=after_close)
    return MarketDataScheduler(settings)


def _at(hour: int, minute: int) -> dt.datetime:
    return dt.datetime.combine(MOSCOW_DAY, dt.time(hour, minute))


def test_before_close_is_not_time() -> None:
    """Собирать до закрытия нельзя: незавершённая сессия — утечка будущего."""
    assert _scheduler()._is_time_to_ingest(_at(12, 0)) is False


def test_just_before_configured_time_is_not_time() -> None:
    assert _scheduler()._is_time_to_ingest(_at(19, 29)) is False


def test_at_configured_time_is_time() -> None:
    assert _scheduler()._is_time_to_ingest(_at(19, 30)) is True


def test_after_configured_time_is_time() -> None:
    assert _scheduler()._is_time_to_ingest(_at(23, 0)) is True


def test_second_run_same_day_is_skipped() -> None:
    """Одна сессия — один сбор: дневные бары уже не изменятся."""
    scheduler = _scheduler()
    assert scheduler._is_time_to_ingest(_at(19, 30)) is True
    scheduler._last_ingested_date = MOSCOW_DAY
    assert scheduler._is_time_to_ingest(_at(21, 0)) is False


def test_next_day_runs_again() -> None:
    scheduler = _scheduler()
    scheduler._last_ingested_date = MOSCOW_DAY
    next_day = dt.datetime.combine(MOSCOW_DAY + dt.timedelta(days=1), dt.time(19, 30))
    assert scheduler._is_time_to_ingest(next_day) is True


def test_custom_time_is_respected() -> None:
    scheduler = _scheduler("21:00")
    assert scheduler._is_time_to_ingest(_at(20, 0)) is False
    assert scheduler._is_time_to_ingest(_at(21, 0)) is True


def test_malformed_time_falls_back_without_crashing() -> None:
    """Опечатка в настройке не должна останавливать сбор навсегда."""
    scheduler = _scheduler("не время")
    assert scheduler._is_time_to_ingest(_at(19, 30)) is True
    assert scheduler._is_time_to_ingest(_at(12, 0)) is False


async def test_disabled_scheduler_does_not_start() -> None:
    settings = Settings(market_data_enabled=False)
    scheduler = MarketDataScheduler(settings)
    await scheduler.start()
    await scheduler.stop()
    assert scheduler._task is None
