"""Общие фикстуры тестов эмулятора."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

ASOF = "2026-08-28"
DIGEST = "sha256:9f2c00000000000000000000000000000000000000000000000000000000abcd"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Клиент приложения. Вселенная больше не конфигурируется."""
    from daily_ml_emulator.app import app

    with TestClient(app) as test_client:
        yield test_client


def asset(ticker: str) -> dict[str, str]:
    return {"asset_id": f"EQ_AST_{ticker}", "price_series_id": f"EQ_PRS_{ticker}"}


def request_body(
    tickers: tuple[str, ...] = ("SBER", "GAZP", "LKOH"),
    asof: str = ASOF,
    digest: str = DIGEST,
    incomplete: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Запрос по contracts/daily-ml-request.md.

    `incomplete` присутствует всегда: пустой список — значимое утверждение
    «окно полно», а не умолчание (spec 004, FR-021).
    """
    return {
        "asof_date": asof,
        "dataset": {
            "ref": "file:///datasets/2026-08-28-9f2c000000000000",
            "digest": digest,
            "windows": {
                "price_sessions": 314,
                "aggregate_sessions": 314,
                "global_sessions": 314,
                "positions_sessions": 82,
            },
            "incomplete": incomplete if incomplete is not None else [],
        },
        "assets": [asset(t) for t in tickers],
    }
