"""Тесты клиента звена ранжирования.

Проверяется контракт запроса и разделение сбоев: недоступность ранжирования —
не то же самое, что сбой сбора данных, и путать их нельзя.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from financial_ai.config import Settings
from financial_ai.ranking.client import (
    RankingUnavailableError,
    build_request,
    request_ranking,
)
from financial_ai.ranking.dataset import AssetRef, Dataset

ASOF = dt.date(2026, 8, 28)
DIGEST = "sha256:abc123"
URL = "http://daily-ml-emulator:8000"


@pytest.fixture
def settings() -> Settings:
    return Settings(daily_ml_url=URL, daily_ml_timeout_seconds=1.0)


@pytest.fixture
def dataset(tmp_path: Path) -> Dataset:
    return Dataset(
        ref=(tmp_path / "ds").as_uri(),
        digest=DIGEST,
        asof_date=ASOF,
        sessions=[ASOF],
        assets=[
            AssetRef("EQ_AST_SBER", "EQ_PRS_SBER"),
            AssetRef("EQ_AST_GAZP", "EQ_PRS_GAZP"),
        ],
        windows={"price_sessions": 314, "global_sessions": 314, "positions_sessions": 82},
        path=tmp_path / "ds",
        incomplete=[],
    )


def _ok_body(digest: str = DIGEST) -> dict[str, object]:
    return {
        "asof_date": ASOF.isoformat(),
        "model_id": "daily-ml-emulator-v1",
        "input_digest": digest,
        "emulated": True,
        "included_asset_count": 2,
        "excluded": [],
        "items": [
            {
                "rank": 1,
                "asset_id": "EQ_AST_SBER",
                "price_series_id": "EQ_PRS_SBER",
                "score": "0.6667",
            },
            {
                "rank": 2,
                "asset_id": "EQ_AST_GAZP",
                "price_series_id": "EQ_PRS_GAZP",
                "score": "0.3333",
            },
        ],
    }


# --- контракт запроса --------------------------------------------------------


def test_request_carries_reference_not_series(dataset: Dataset) -> None:
    """Ряды в теле не передаются: из 314 сессий новой является одна."""
    payload = build_request(dataset)
    assert payload["dataset"]["ref"] == dataset.ref  # type: ignore[index]
    assert payload["dataset"]["digest"] == DIGEST  # type: ignore[index]
    assert "prices" not in payload
    assert "bars" not in payload


def test_request_lists_assets(dataset: Dataset) -> None:
    payload = build_request(dataset)
    assert [a["asset_id"] for a in payload["assets"]] == ["EQ_AST_SBER", "EQ_AST_GAZP"]  # type: ignore[union-attr]


def test_request_does_not_decide_eligibility(dataset: Dataset) -> None:
    """Допустимость определяет сторона модели — отправитель не решает за неё."""
    payload = build_request(dataset)
    asset = payload["assets"][0]  # type: ignore[index]
    assert set(asset) == {"asset_id", "price_series_id"}


# --- разбор ответа -----------------------------------------------------------


@respx.mock
async def test_ranking_is_parsed(settings: Settings, dataset: Dataset) -> None:
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(200, json=_ok_body()))
    ranking = await request_ranking(settings, dataset)
    assert ranking.included_asset_count == 2
    assert ranking.items[0].asset_id == "EQ_AST_SBER"
    assert ranking.items[0].score == Decimal("0.6667")


@respx.mock
async def test_score_stays_exact(settings: Settings, dataset: Dataset) -> None:
    body = _ok_body()
    body["items"][0]["score"] = "0.123456789"  # type: ignore[index]
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(200, json=body))
    ranking = await request_ranking(settings, dataset)
    assert ranking.items[0].score == Decimal("0.123456789")


@respx.mock
async def test_emulation_flag_is_visible(settings: Settings, dataset: Dataset) -> None:
    """Признак эмуляции должен доходить до вызывающей стороны."""
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(200, json=_ok_body()))
    ranking = await request_ranking(settings, dataset)
    assert ranking.emulated is True


# --- сверка дайджеста --------------------------------------------------------


@respx.mock
async def test_digest_mismatch_is_refused(settings: Settings, dataset: Dataset) -> None:
    """Иначе ранжирование могло бы относиться к другим данным."""
    respx.post(f"{URL}/rankings").mock(
        return_value=httpx.Response(200, json=_ok_body(digest="sha256:другой"))
    )
    with pytest.raises(RankingUnavailableError, match="не совпадает"):
        await request_ranking(settings, dataset)


@respx.mock
async def test_unknown_asset_in_response_is_refused(settings: Settings, dataset: Dataset) -> None:
    body = _ok_body()
    body["items"].append(  # type: ignore[union-attr]
        {"rank": 3, "asset_id": "EQ_AST_НЕТ", "price_series_id": "EQ_PRS_НЕТ", "score": "0.1"}
    )
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(RankingUnavailableError, match="которых не было в запросе"):
        await request_ranking(settings, dataset)


# --- разделение сбоев --------------------------------------------------------


@respx.mock
async def test_unavailable_service_raises_ranking_error(
    settings: Settings, dataset: Dataset
) -> None:
    """FR-025: сбой ранжирования отличим от сбоя сбора данных."""
    respx.post(f"{URL}/rankings").mock(side_effect=httpx.ConnectError("нет связи"))
    with pytest.raises(RankingUnavailableError, match="недоступно"):
        await request_ranking(settings, dataset)


@respx.mock
async def test_server_error_raises_ranking_error(settings: Settings, dataset: Dataset) -> None:
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(500, text="упало"))
    with pytest.raises(RankingUnavailableError, match="500"):
        await request_ranking(settings, dataset)


@respx.mock
async def test_malformed_response_raises_ranking_error(
    settings: Settings, dataset: Dataset
) -> None:
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(200, text="не json"))
    with pytest.raises(RankingUnavailableError, match="не является JSON"):
        await request_ranking(settings, dataset)


@respx.mock
async def test_response_without_items_raises(settings: Settings, dataset: Dataset) -> None:
    respx.post(f"{URL}/rankings").mock(
        return_value=httpx.Response(200, json={"input_digest": DIGEST})
    )
    with pytest.raises(RankingUnavailableError, match="нет списка items"):
        await request_ranking(settings, dataset)


@respx.mock
async def test_excluded_assets_are_reported(settings: Settings, dataset: Dataset) -> None:
    """Расхождение между отправленным и ранжированным не должно быть молчаливым."""
    body = _ok_body()
    body["items"] = [body["items"][0]]  # type: ignore[index]
    body["excluded"] = [{"asset_id": "EQ_AST_GAZP", "reason": "нет истории"}]
    respx.post(f"{URL}/rankings").mock(return_value=httpx.Response(200, json=body))

    ranking = await request_ranking(settings, dataset)
    assert ranking.included_asset_count == 1
    assert ranking.excluded[0]["asset_id"] == "EQ_AST_GAZP"
    assert ranking.excluded[0]["reason"] == "нет истории"


# --- объявление полноты окна (FR-021, spec 004) ------------------------------


def test_request_always_declares_completeness(dataset: Dataset) -> None:
    """Пустой перечень — значимое утверждение «окно полно», а не отсутствие."""
    payload = build_request(dataset)
    assert payload["dataset"]["incomplete"] == []  # type: ignore[index]


def test_request_carries_incomplete_sessions(tmp_path: Path) -> None:
    incomplete = [
        {"session_date": "2026-08-14", "sources": ["equity_d1"]},
        {"session_date": "2026-08-17", "sources": ["futures_positions"]},
    ]
    dataset = Dataset(
        ref=(tmp_path / "ds").as_uri(),
        digest=DIGEST,
        asof_date=ASOF,
        sessions=[ASOF],
        assets=[AssetRef("EQ_AST_SBER", "EQ_PRS_SBER")],
        windows={"price_sessions": 314},
        path=tmp_path / "ds",
        incomplete=incomplete,
    )

    payload = build_request(dataset)

    assert payload["dataset"]["incomplete"] == incomplete  # type: ignore[index]


def test_incomplete_is_sorted_by_date(tmp_path: Path) -> None:
    """Порядок фиксирован: одинаковое содержимое обязано давать один дайджест."""
    incomplete = [
        {"session_date": "2026-08-14", "sources": ["equity_d1"]},
        {"session_date": "2026-08-17", "sources": ["equity_d1"]},
    ]
    dataset = Dataset(
        ref=(tmp_path / "ds").as_uri(),
        digest=DIGEST,
        asof_date=ASOF,
        sessions=[ASOF],
        assets=[AssetRef("EQ_AST_SBER", "EQ_PRS_SBER")],
        windows={"price_sessions": 314},
        path=tmp_path / "ds",
        incomplete=incomplete,
    )

    rows = build_request(dataset)["dataset"]["incomplete"]  # type: ignore[index]
    dates = [row["session_date"] for row in rows]
    assert dates == sorted(dates)
