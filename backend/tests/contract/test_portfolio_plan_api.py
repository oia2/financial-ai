"""Контракт маршрутов плана — contracts/portfolio-plan-api.md.

Публичная граница раздела «План портфеля». Проверяется то, ради чего она
заведена: перечень правил задаёт сервер, расчёт отдаёт числа строками и
называет сессию цены, каждый отказ доходит своим кодом — и маршрутов исполнения
в этой границе нет.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings, get_settings

from ..portfolio_plan.conftest import position, seed_account, seed_market, seed_ranking

pytestmark = pytest.mark.db

PLAN = "/api/portfolio-plan"


@pytest.fixture
def settings(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Настройки стенда плана.

    Те же значения задаются и окружению: признак устаревания ранжирования
    считается пересборкой набора, и её выполняет **маршрут**, читая настройки
    процесса. Без этого корень наборов остался бы производственным.
    """
    from ..portfolio_plan.conftest import SESSIONS

    values = {
        "MARKET_DATA_PRICE_WINDOW_SESSIONS": str(len(SESSIONS)),
        "MARKET_DATA_GLOBAL_WINDOW_SESSIONS": str(len(SESSIONS)),
        "MARKET_DATA_POSITIONS_WINDOW_SESSIONS": str(len(SESSIONS)),
        "MARKET_DATA_DATASET_ROOT": str(tmp_path),
        "DAILY_ML_REQUIRED_DATA_GROUPS": '["quotes"]',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


async def test_policies_are_defined_by_the_server(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Произвольное правило человеком не вводится: веса проверены исследованием."""
    body = (await api_client.get("/api/portfolio-plan/policies")).json()

    ids = [policy["id"] for policy in body["policies"]]
    assert "equal_top20" in ids
    assert "rank_zones_100" in ids
    assert body["default_policy"] == "equal_top20"
    # Каждое правило названо источником: откуда взяты веса, видно из ответа.
    assert all(policy["source"] for policy in body["policies"])


async def test_plan_carries_prices_with_their_session(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Цена — закрытие названной сессии, а не текущая котировка."""
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    response = await api_client.post(PLAN, json={"policy": "equal_top20"})

    assert response.status_code == 200
    body = response.json()
    assert body["price_source"] == {"kind": "session_close", "session_date": "2026-09-01"}
    assert response.headers["cache-control"] == "no-store"


async def test_amounts_are_strings(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    """`float` на пути «БД → API → JSON» теряет копейки."""
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    body = (await api_client.post(PLAN, json={"policy": "equal_top20"})).json()

    assert isinstance(body["capital"]["eligible"], str)
    row = body["positions"][0]
    for field in ("price", "target_value", "target_quantity", "delta_value", "delta_quantity"):
        assert isinstance(row[field], str), field
    # Лоты — счётные величины, а не суммы: дробного лота не существует.
    assert isinstance(row["lot_size"], int)
    assert isinstance(row["target_lots"], int)


async def test_untouched_and_excluded_are_always_present(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Оба перечня — утверждения, а не умолчания."""
    await seed_market(db_session, lots={"EQ_AST_LKOH": None})
    await seed_account(
        db_session,
        positions=(position("SU26238RMFS4", Decimal("200"), Decimal("500.00"), asset_type="bond"),),
    )
    await seed_ranking(db_session, settings)

    body = (await api_client.post(PLAN, json={"policy": "equal_top20"})).json()

    assert body["untouched"][0]["reason"] == "вне вселенной модели"
    assert body["excluded"][0]["reason"] == "не известен размер лота"


async def test_limit_and_fee_are_accepted_as_strings(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    body = (
        await api_client.post(
            PLAN,
            json={"policy": "equal_top20", "capital_limit": "20000.00", "fee_percent": "0.05"},
        )
    ).json()

    assert Decimal(body["capital"]["limit"]) == Decimal("20000.00")


@pytest.mark.parametrize(
    ("payload", "expected_status", "code"),
    [
        ({"policy": "equal_top7"}, 422, "unknown_policy"),
        ({"policy": "equal_top20", "capital_limit": "ноль"}, 422, "invalid_limit"),
        ({"policy": "equal_top20", "fee_percent": "99"}, 422, "invalid_fee"),
    ],
)
async def test_invalid_settings_are_refused_each_with_its_reason(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
    payload: dict[str, str],
    expected_status: int,
    code: str,
) -> None:
    response = await api_client.post(PLAN, json=payload)

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == code


async def test_missing_ranking_is_a_refusal_not_an_empty_plan(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Пустой состав прочитался бы как «продай всё»."""
    await seed_market(db_session)
    await seed_account(db_session)

    response = await api_client.post(PLAN, json={"policy": "equal_top20"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_successful_ranking"
    assert "positions" not in response.json()


async def test_disconnected_account_is_refused(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    await seed_market(db_session)
    await seed_ranking(db_session, settings)

    response = await api_client.post(PLAN, json={"policy": "equal_top20"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "broker_not_connected"


async def test_stale_snapshot_is_refused(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    await seed_market(db_session)
    await seed_account(db_session, captured_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2))
    await seed_ranking(db_session, settings)

    response = await api_client.post(PLAN, json={"policy": "equal_top20"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "portfolio_stale"


async def test_insufficient_assets_names_required_and_available(
    api_client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings, order=("EQ_AST_SBER", "EQ_AST_LKOH"))

    response = await api_client.post(PLAN, json={"policy": "equal_top20"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_assets"
    assert detail["required"] == 20
    assert detail["available"] == 2


async def test_no_execution_routes_exist(api_client: httpx.AsyncClient) -> None:
    """Расчёт не исполняет: маршрутов исполнения в границе нет (FR-063).

    Проверяется по схеме приложения, а не по одному адресу: новый маршрут
    исполнения появился бы под любым именем, а вот в разделе плана его быть не
    должно вовсе.
    """
    schema = (await api_client.get("/api/openapi.json")).json()

    plan_paths = [path for path in schema["paths"] if path.startswith("/api/portfolio-plan")]
    assert set(plan_paths) == {"/api/portfolio-plan", "/api/portfolio-plan/policies"}

    methods = {
        method
        for path in plan_paths
        for method in schema["paths"][path]
        if method in {"put", "patch", "delete"}
    }
    assert methods == set()
