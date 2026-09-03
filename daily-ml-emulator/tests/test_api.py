"""Контрактные тесты HTTP-интерфейса.

Сверяются с specs/002-daily-ml-emulator/contracts/daily-ml-emulator-api.md.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

DATE = "2026-08-28"


# --- T013: контракт GET /rankings -------------------------------------------------


def test_rankings_returns_200(client: TestClient) -> None:
    assert client.get(f"/rankings?decision_date={DATE}").status_code == 200


def test_response_has_all_contract_fields(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert set(body) == {
        "decision_date",
        "model_id",
        "generated_at",
        "emulated",
        "emulation_notice",
        "items",
    }


def test_item_has_all_contract_fields(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    for item in body["items"]:
        assert set(item) == {"rank", "asset_id", "price_series_id", "score"}


def test_decision_date_is_echoed(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert body["decision_date"] == DATE


def test_items_cover_whole_universe(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert len(body["items"]) == 10


def test_ranks_are_contiguous_and_sorted(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert [item["rank"] for item in body["items"]] == list(range(1, 11))


def test_asset_id_is_unique_within_response(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    asset_ids = [item["asset_id"] for item in body["items"]]
    assert len(set(asset_ids)) == len(asset_ids)


def test_score_is_transmitted_as_string(client: TestClient) -> None:
    """FR-007: строка, а не число — значение у потребителя точно совпадает с выданным."""
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert all(isinstance(item["score"], str) for item in body["items"])


def test_scores_strictly_decrease(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    scores = [Decimal(item["score"]) for item in body["items"]]
    assert scores == sorted(scores, reverse=True)


def test_model_id_comes_from_settings(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert body["model_id"] == "daily-ml-emulator-v1"


def test_model_id_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-016: идентификатор модели задаётся конфигурацией, без пересборки образа."""
    from pathlib import Path

    universe = Path(__file__).resolve().parent.parent / "universe" / "default.json"
    monkeypatch.setenv("DAILY_ML_EMULATOR_UNIVERSE_PATH", str(universe))
    monkeypatch.setenv("DAILY_ML_EMULATOR_MODEL_ID", "daily-ml-emulator-испытание")

    from daily_ml_emulator.app import app

    with TestClient(app) as configured:
        body = configured.get(f"/rankings?decision_date={DATE}").json()

    assert body["model_id"] == "daily-ml-emulator-испытание"


def test_generated_at_is_utc(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert body["generated_at"].endswith("Z")


def test_same_date_gives_same_items(client: TestClient) -> None:
    """FR-009: между запросами меняется только generated_at."""
    first = client.get(f"/rankings?decision_date={DATE}").json()
    second = client.get(f"/rankings?decision_date={DATE}").json()
    assert first["items"] == second["items"]


def test_different_dates_give_different_order(client: TestClient) -> None:
    first = client.get("/rankings?decision_date=2026-08-28").json()
    second = client.get("/rankings?decision_date=2026-08-29").json()
    assert first["items"][0]["asset_id"] != second["items"][0]["asset_id"]


def test_non_trading_day_is_answered(client: TestClient) -> None:
    """Торгового календаря эмулятор не знает и отвечает на любую корректную дату."""
    assert client.get("/rankings?decision_date=2026-01-01").status_code == 200


def test_far_future_date_is_answered(client: TestClient) -> None:
    assert client.get("/rankings?decision_date=2099-12-31").status_code == 200


# --- T014: признак эмуляции -------------------------------------------------------


def test_response_is_marked_as_emulated(client: TestClient) -> None:
    """FR-006: вымышленные скоры нельзя принять за результат настоящей модели."""
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert body["emulated"] is True


def test_emulation_notice_is_present_and_not_empty(client: TestClient) -> None:
    body = client.get(f"/rankings?decision_date={DATE}").json()
    assert body["emulation_notice"].strip()


@pytest.mark.parametrize("decision_date", ["2026-08-28", "2026-08-29", "2099-12-31"])
def test_every_successful_response_is_marked(client: TestClient, decision_date: str) -> None:
    body = client.get(f"/rankings?decision_date={decision_date}").json()
    assert body["emulated"] is True


# --- T015: ошибки -----------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "?decision_date=28.08.2026",
        "?decision_date=2026-13-01",
        "?decision_date=",
        "?decision_date=вчера",
        "",
    ],
)
def test_invalid_decision_date_returns_422(client: TestClient, query: str) -> None:
    response = client.get(f"/rankings{query}")
    assert response.status_code == 422


@pytest.mark.parametrize("query", ["?decision_date=28.08.2026", ""])
def test_error_body_has_project_shape(client: TestClient, query: str) -> None:
    body = client.get(f"/rankings{query}").json()
    assert body["detail"]["code"] == "invalid_decision_date"
    assert body["detail"]["message"]


def test_no_ranking_is_returned_on_error(client: TestClient) -> None:
    """FR-013: при некорректном запросе ранжирование не выдаётся."""
    body = client.get("/rankings?decision_date=28.08.2026").json()
    assert "items" not in body


def test_unknown_path_returns_404_in_project_shape(client: TestClient) -> None:
    """Прочие ошибки приходят в той же форме — второго формата потребителю не нужно."""
    response = client.get("/нет-такого-пути")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "error"


def test_unsupported_method_returns_405_in_project_shape(client: TestClient) -> None:
    response = client.post("/rankings")
    assert response.status_code == 405
    assert response.json()["detail"]["code"] == "error"


# --- T021: health -----------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- FR-022: схема эндпоинтов от самого сервиса -----------------------------------


def test_openapi_schema_is_served(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/rankings" in schema["paths"]
    assert "/health" in schema["paths"]


def test_docs_page_is_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
