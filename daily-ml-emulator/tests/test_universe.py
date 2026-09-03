"""Тесты конфигурации вселенной активов.

Правила валидации — в specs/002-daily-ml-emulator/data-model.md §1. Все они проверяются
при старте: непригодная конфигурация не должна давать контейнеру подняться.
"""

import json
from pathlib import Path

import pytest

from daily_ml_emulator.universe import UniverseEntry, UniverseError, load_universe


def write_universe(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "universe.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def entry(ticker: str) -> dict[str, str]:
    return {"asset_id": f"EQ_AST_{ticker}", "price_series_id": f"EQ_PRS_{ticker}"}


# --- T031: отказ на непригодной конфигурации --------------------------------------


def test_empty_universe_is_rejected(tmp_path: Path) -> None:
    path = write_universe(tmp_path, [])
    with pytest.raises(UniverseError, match="пуст"):
        load_universe(path)


def test_duplicate_asset_id_is_rejected(tmp_path: Path) -> None:
    """Иначе рушится уникальность decision_date + asset_id (FR-008)."""
    path = write_universe(
        tmp_path,
        [
            entry("SBER"),
            {"asset_id": "EQ_AST_SBER", "price_series_id": "EQ_PRS_SBER_ALT"},
        ],
    )
    with pytest.raises(UniverseError, match="asset_id"):
        load_universe(path)


def test_duplicate_price_series_id_is_rejected(tmp_path: Path) -> None:
    path = write_universe(
        tmp_path,
        [
            entry("SBER"),
            {"asset_id": "EQ_AST_GAZP", "price_series_id": "EQ_PRS_SBER"},
        ],
    )
    with pytest.raises(UniverseError, match="price_series_id"):
        load_universe(path)


@pytest.mark.parametrize(
    "broken",
    [
        {"asset_id": "", "price_series_id": "EQ_PRS_SBER"},
        {"asset_id": "EQ_AST_SBER", "price_series_id": ""},
        {"asset_id": "   ", "price_series_id": "EQ_PRS_SBER"},
        {"price_series_id": "EQ_PRS_SBER"},
        {"asset_id": "EQ_AST_SBER"},
    ],
)
def test_empty_or_missing_fields_are_rejected(tmp_path: Path, broken: dict[str, str]) -> None:
    path = write_universe(tmp_path, [broken])
    with pytest.raises(UniverseError):
        load_universe(path)


def test_oversized_universe_is_rejected(tmp_path: Path) -> None:
    """Граница 200 активов взята из SC-002."""
    path = write_universe(tmp_path, [entry(f"T{index:04d}") for index in range(201)])
    with pytest.raises(UniverseError, match="200"):
        load_universe(path)


def test_universe_of_exactly_two_hundred_is_accepted(tmp_path: Path) -> None:
    path = write_universe(tmp_path, [entry(f"T{index:04d}") for index in range(200)])
    assert len(load_universe(path)) == 200


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "universe.json"
    path.write_text("{не json", encoding="utf-8")
    with pytest.raises(UniverseError, match="JSON"):
        load_universe(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UniverseError, match="прочитать"):
        load_universe(tmp_path / "нет-такого-файла.json")


def test_non_list_document_is_rejected(tmp_path: Path) -> None:
    path = write_universe(tmp_path, {"asset_id": "EQ_AST_SBER"})
    with pytest.raises(UniverseError, match="список"):
        load_universe(path)


def test_non_object_entry_is_rejected(tmp_path: Path) -> None:
    path = write_universe(tmp_path, ["EQ_AST_SBER"])
    with pytest.raises(UniverseError, match="объектом"):
        load_universe(path)


def test_error_message_names_the_offending_entry(tmp_path: Path) -> None:
    """Сообщение должно называть причину и конкретную запись, а не «что-то не так»."""
    path = write_universe(tmp_path, [entry("SBER"), entry("SBER")])
    with pytest.raises(UniverseError) as error:
        load_universe(path)
    assert "EQ_AST_SBER" in str(error.value)


# --- T032: применение конфигурации ------------------------------------------------


def test_default_universe_is_valid(default_universe_path: Path) -> None:
    universe = load_universe(default_universe_path)
    assert len(universe) == 10
    assert universe[0] == UniverseEntry(asset_id="EQ_AST_SBER", price_series_id="EQ_PRS_SBER")


def test_entry_order_is_preserved(tmp_path: Path) -> None:
    """Порядок значим: он задаёт базовую нумерацию для сдвига."""
    path = write_universe(tmp_path, [entry("GAZP"), entry("SBER"), entry("LKOH")])
    universe = load_universe(path)
    assert [item.asset_id for item in universe] == [
        "EQ_AST_GAZP",
        "EQ_AST_SBER",
        "EQ_AST_LKOH",
    ]


def test_configured_universe_is_used_by_the_endpoint(make_client) -> None:  # type: ignore[no-untyped-def]
    """FR-016: ответ содержит ровно заданные активы, без добавленных и пропущенных."""
    client = make_client([entry("SBER"), entry("GAZP")])
    body = client.get("/rankings?decision_date=2026-08-28").json()
    assert len(body["items"]) == 2
    assert {item["asset_id"] for item in body["items"]} == {"EQ_AST_SBER", "EQ_AST_GAZP"}
    assert [item["rank"] for item in body["items"]] == [1, 2]


def test_single_asset_universe_is_served(make_client) -> None:  # type: ignore[no-untyped-def]
    client = make_client([entry("SBER")])
    body = client.get("/rankings?decision_date=2026-08-28").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["rank"] == 1
