"""Общие фикстуры тестов эмулятора."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_UNIVERSE = Path(__file__).resolve().parent.parent / "universe" / "default.json"


@pytest.fixture
def default_universe_path() -> Path:
    """Файл вселенной по умолчанию, поставляемый с эмулятором."""
    return REPO_UNIVERSE


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Клиент приложения на вселенной по умолчанию."""
    monkeypatch.setenv("DAILY_ML_EMULATOR_UNIVERSE_PATH", str(REPO_UNIVERSE))
    from daily_ml_emulator.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[object]:
    """Фабрика клиентов на произвольной вселенной.

    Возвращает вызываемый объект: список записей -> TestClient.
    """
    clients: list[TestClient] = []

    def _make(entries: list[dict[str, str]] | str) -> TestClient:
        universe_file = tmp_path / "universe.json"
        if isinstance(entries, str):
            universe_file.write_text(entries, encoding="utf-8")
        else:
            universe_file.write_text(json.dumps(entries), encoding="utf-8")
        monkeypatch.setenv("DAILY_ML_EMULATOR_UNIVERSE_PATH", str(universe_file))
        from daily_ml_emulator.app import app

        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield _make

    for test_client in clients:
        test_client.__exit__(None, None, None)
