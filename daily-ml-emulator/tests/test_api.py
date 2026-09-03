"""Контрактные тесты HTTP-интерфейса.

Сверяются с specs/003-moex-data-ingestion/contracts/daily-ml-request.md.
Это будущий контракт обученной модели: она встанет в контейнере на это место.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from conftest import ASOF, DIGEST, request_body

# --- контракт ответа ---------------------------------------------------------


def test_rankings_returns_200(client: TestClient) -> None:
    assert client.post("/rankings", json=request_body()).status_code == 200


def test_response_has_all_contract_fields(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert set(body) == {
        "asof_date",
        "model_id",
        "input_digest",
        "generated_at",
        "emulated",
        "emulation_notice",
        "included_asset_count",
        "excluded",
        "items",
    }


def test_item_has_all_contract_fields(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    for item in body["items"]:
        assert set(item) == {"rank", "asset_id", "price_series_id", "score"}


def test_asof_date_is_echoed(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert body["asof_date"] == ASOF


def test_ranks_are_contiguous_and_sorted(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert [i["rank"] for i in body["items"]] == [1, 2, 3]


def test_scores_strictly_decrease(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    scores = [Decimal(i["score"]) for i in body["items"]]
    assert scores == sorted(scores, reverse=True)


def test_score_is_transmitted_as_string(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert all(isinstance(i["score"], str) for i in body["items"])


# --- активы приходят из запроса ---------------------------------------------


def test_ranks_assets_from_request_not_configuration(client: TestClient) -> None:
    """FR-027: список из конфигурации не имел отношения к торговавшемуся."""
    body = client.post("/rankings", json=request_body(("SBER", "GAZP"))).json()
    assert {i["asset_id"] for i in body["items"]} == {"EQ_AST_SBER", "EQ_AST_GAZP"}


def test_response_contains_no_unrequested_assets(client: TestClient) -> None:
    sent = {"EQ_AST_SBER", "EQ_AST_GAZP"}
    body = client.post("/rankings", json=request_body(("SBER", "GAZP"))).json()
    assert {i["asset_id"] for i in body["items"]} <= sent


def test_price_series_id_is_carried_through(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body(("SBER",))).json()
    assert body["items"][0]["price_series_id"] == "EQ_PRS_SBER"


def test_single_asset_is_ranked(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body(("SBER",))).json()
    assert body["included_asset_count"] == 1
    assert body["items"][0]["rank"] == 1


def test_many_assets_are_ranked(client: TestClient) -> None:
    tickers = tuple(f"T{i:04d}" for i in range(288))
    body = client.post("/rankings", json=request_body(tickers)).json()
    assert body["included_asset_count"] == 288
    assert [i["rank"] for i in body["items"]] == list(range(1, 289))


# --- дайджест входа ----------------------------------------------------------


def test_input_digest_is_echoed(client: TestClient) -> None:
    """FR-021: без этого нельзя доказать, на каких данных получено ранжирование."""
    body = client.post("/rankings", json=request_body()).json()
    assert body["input_digest"] == DIGEST


def test_different_digest_is_echoed_back(client: TestClient) -> None:
    other = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    body = client.post("/rankings", json=request_body(digest=other)).json()
    assert body["input_digest"] == other


# --- полнота учёта -----------------------------------------------------------


def test_every_sent_asset_is_accounted_for(client: TestClient) -> None:
    """FR-024: расхождение между отправленным и ранжированным не должно быть молчаливым."""
    sent = {"EQ_AST_SBER", "EQ_AST_GAZP", "EQ_AST_LKOH"}
    body = client.post("/rankings", json=request_body()).json()
    accounted = {i["asset_id"] for i in body["items"]} | {e["asset_id"] for e in body["excluded"]}
    assert accounted == sent


def test_included_count_matches_items(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert body["included_asset_count"] == len(body["items"])


# --- эмулятор не читает набор ------------------------------------------------


def test_emulator_answers_with_unreachable_dataset_ref(client: TestClient) -> None:
    """FR-028: эмулятор ничего не вычисляет, поэтому набор ему не нужен."""
    payload = request_body()
    payload["dataset"]["ref"] = "file:///нет-такого-каталога/вообще"
    assert client.post("/rankings", json=payload).status_code == 200


# --- признак эмуляции --------------------------------------------------------


def test_response_is_marked_as_emulated(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert body["emulated"] is True


def test_emulation_notice_is_not_empty(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body()).json()
    assert body["emulation_notice"].strip()


# --- детерминизм и зависимость от даты --------------------------------------


def test_same_request_gives_same_items(client: TestClient) -> None:
    first = client.post("/rankings", json=request_body()).json()
    second = client.post("/rankings", json=request_body()).json()
    assert first["items"] == second["items"]


def test_different_dates_give_different_order(client: TestClient) -> None:
    first = client.post("/rankings", json=request_body(asof="2026-08-28")).json()
    second = client.post("/rankings", json=request_body(asof="2026-08-29")).json()
    assert first["items"][0]["asset_id"] != second["items"][0]["asset_id"]


# --- ошибки ------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["28.08.2026", "2026-13-01", "вчера", ""])
def test_invalid_asof_date_returns_422(client: TestClient, bad: str) -> None:
    response = client.post("/rankings", json=request_body(asof=bad))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_asof_date"


def test_missing_dataset_returns_422(client: TestClient) -> None:
    payload = request_body()
    del payload["dataset"]
    response = client.post("/rankings", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_empty_assets_returns_422(client: TestClient) -> None:
    payload = request_body()
    payload["assets"] = []
    response = client.post("/rankings", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_duplicate_asset_returns_422(client: TestClient) -> None:
    """Ключ asof_date + asset_id обязан остаться ключом."""
    payload = request_body(("SBER",))
    payload["assets"].append({"asset_id": "EQ_AST_SBER", "price_series_id": "EQ_PRS_ДРУГОЙ"})
    response = client.post("/rankings", json=payload)
    assert response.status_code == 422
    assert "более одного раза" in response.json()["detail"]["message"]


def test_asset_without_id_returns_422(client: TestClient) -> None:
    payload = request_body()
    payload["assets"] = [{"price_series_id": "EQ_PRS_SBER"}]
    assert client.post("/rankings", json=payload).status_code == 422


def test_no_ranking_is_returned_on_error(client: TestClient) -> None:
    body = client.post("/rankings", json=request_body(asof="28.08.2026")).json()
    assert "items" not in body


def test_get_rankings_is_gone(client: TestClient) -> None:
    """Прежний контракт заменён: вселенная больше не берётся из конфигурации."""
    assert client.get("/rankings?decision_date=2026-08-28").status_code == 405


# --- служебное ---------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_is_served(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/rankings" in schema["paths"]
    assert "post" in schema["paths"]["/rankings"]


def test_docs_page_is_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_unknown_path_returns_404_in_project_shape(client: TestClient) -> None:
    response = client.get("/нет-такого-пути")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "error"


def test_emulator_needs_no_database_or_secrets() -> None:
    """FR-029: у звена ранжирования нет ни реквизитов доступа, ни хранилища."""
    from daily_ml_emulator.config import Settings

    fields = set(Settings.model_fields)
    assert not any(
        any(marker in name.upper() for marker in ("TOKEN", "PASSWORD", "DATABASE", "SECRET"))
        for name in fields
    )
