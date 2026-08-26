"""Порог устаревания данных (T068, FR-040)."""

from __future__ import annotations

import datetime as dt

import pytest

from financial_ai.domain.portfolio import (
    MIN_STALE_AFTER_SECONDS,
    STALE_INTERVAL_FACTOR,
    age_seconds,
    is_stale,
    stale_after_seconds,
)

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        (15, 180),
        (30, 180),
        (60, 180),
        (61, 183),
        (120, 360),
        (600, 1800),
        (3600, 10800),
    ],
)
def test_threshold_is_max_of_triple_interval_and_floor(interval: int, expected: int) -> None:
    assert stale_after_seconds(interval) == expected


def test_floor_prevents_false_alarms_on_small_intervals() -> None:
    # При интервале 15 c троекратное значение — 45 c: слишком чувствительно.
    assert stale_after_seconds(15) == MIN_STALE_AFTER_SECONDS
    assert STALE_INTERVAL_FACTOR * 15 < MIN_STALE_AFTER_SECONDS


@pytest.mark.parametrize(
    ("age", "interval", "expected"),
    [
        (0, 60, False),
        (179, 60, False),
        (180, 60, False),
        (181, 60, True),
        (359, 120, False),
        (361, 120, True),
    ],
)
def test_is_stale_compares_age_with_threshold(age: int, interval: int, expected: bool) -> None:
    captured = NOW - dt.timedelta(seconds=age)

    assert is_stale(captured, NOW, interval) is expected


def test_absent_snapshot_is_not_stale() -> None:
    # «Данных ещё нет» — отдельное состояние, а не «данные несвежие» (US4 AS4).
    assert is_stale(None, NOW, 60) is False
    assert age_seconds(None, NOW) is None


def test_fresh_data_after_successful_sync_is_not_stale() -> None:
    assert is_stale(NOW, NOW, 60) is False
