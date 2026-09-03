"""Тесты клиента MOEX ISS.

Обращения к бирже подменяются через respx: сеть для прогона тестов не нужна.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from financial_ai.market_data.iss.client import IssClient, IssConfig, IssError

BASE = "https://iss.moex.com/iss"
COLUMNS = ("SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")


def _page(rows: list[list[object]]) -> dict[str, object]:
    return {"history": {"columns": list(COLUMNS), "data": rows}}


def _row(secid: str, date: str = "2026-08-28") -> list[object]:
    return [secid, date, "312.4", "315.1", "311.0", "314.22", "1000"]


@pytest.fixture
def config() -> IssConfig:
    # Небольшие значения: тесты не должны ждать по-настоящему.
    return IssConfig(
        base_url=BASE, page_limit=2, retries=3, timeout_seconds=1.0, initial_retry_delay_seconds=0.0
    )


@respx.mock
async def test_single_page(config: IssConfig) -> None:
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_page([_row("SBER")]))
    )
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert len(rows) == 1
    assert rows[0]["SECID"] == "SBER"


@respx.mock
async def test_pagination_walks_all_pages(config: IssConfig) -> None:
    """Пока страница полная, клиент запрашивает следующую."""
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(200, json=_page([_row("SBER"), _row("GAZP")])),
        httpx.Response(200, json=_page([_row("LKOH")])),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert [r["SECID"] for r in rows] == ["SBER", "GAZP", "LKOH"]
    assert route.call_count == 2


@respx.mock
async def test_empty_response_stops_pagination(config: IssConfig) -> None:
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=_page([])))
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert rows == []


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_retries_on_retryable_status(config: IssConfig, status: int) -> None:
    """429 в списке повторяемых не случайно: биржа ограничивает частоту."""
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(status),
        httpx.Response(200, json=_page([_row("SBER")])),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert len(rows) == 1
    assert route.call_count == 2


@respx.mock
async def test_no_retry_on_client_error(config: IssConfig) -> None:
    """404 повтором не лечится — незачем долбить биржу."""
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(404))
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="404"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert route.call_count == 1


@respx.mock
async def test_fails_after_retries_exhausted(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(503))
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="после 3 попыток"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert route.call_count == 3


@respx.mock
async def test_network_error_is_retried(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.ConnectError("нет связи"),
        httpx.Response(200, json=_page([_row("SBER")])),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert len(rows) == 1


@respx.mock
async def test_non_json_response_fails_clearly(config: IssConfig) -> None:
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, text="<html>не json"))
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="не является JSON"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
async def test_backfill_uses_security_form(config: IssConfig) -> None:
    """Первичная загрузка ходит по бумаге, а не по дате."""
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_page([_row("SBER")]))
    )
    async with IssClient(config) as client:
        await client.fetch_security_history("SBER", "1990-01-01", "2026-08-28", COLUMNS)
    assert "securities/SBER.json" in str(route.calls[0].request.url)


async def test_client_requires_context_manager(config: IssConfig) -> None:
    client = IssClient(config)
    with pytest.raises(IssError, match="не инициализирован"):
        await client.fetch_session_rows("2026-08-28", COLUMNS)
