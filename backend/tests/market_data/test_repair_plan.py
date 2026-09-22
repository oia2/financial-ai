"""Regression tests for persisted, addressable repair plans."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from financial_ai.market_data import repair_audit


@pytest.mark.asyncio
async def test_completed_plan_returns_before_opening_collection_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal plan is a read-only result, even with budget left."""

    class Repository:
        def __init__(self, session: object) -> None:
            self.session = session

        async def repair_plan(self, plan_id: str) -> tuple[object, list[object]]:
            return SimpleNamespace(status="completed", requests_spent=1, request_budget=2), [
                SimpleNamespace(status="pending")
            ]

    monkeypatch.setattr(repair_audit, "MarketDataRepository", Repository)

    result = await repair_audit._run_plan_owned(object(), object(), "completed-plan")

    assert result == ("completed", 1, 0)
