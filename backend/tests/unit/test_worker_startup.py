"""Готовность HTTP worker не ждёт завершения первого ранжирования."""

from __future__ import annotations

import asyncio

import pytest

from financial_ai.config import Settings
from financial_ai.daily_ml.scheduler import DailyMlScheduler


async def test_start_returns_while_initial_ranking_is_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    scheduler = DailyMlScheduler(Settings(daily_ml_enabled=True))

    async def slow_tick() -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(scheduler, "tick", slow_tick)
    try:
        await asyncio.wait_for(scheduler.start(), timeout=1)
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not release.is_set()
    finally:
        release.set()
        await scheduler.stop()
