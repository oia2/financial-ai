"""Тесты клиента MOEX ISS.

Обращения к бирже подменяются через respx: сеть для прогона тестов не нужна.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from financial_ai.market_data.interrupt import SourceStoppedError
from financial_ai.market_data.iss.client import IssClient, IssConfig, IssError

BASE = "https://iss.moex.com/iss"
COLUMNS = ("SECID", "TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")


def _page(
    rows: list[list[object]],
    *,
    index: int = 0,
    total: int | None = None,
    page_size: int = 2,
    cursor: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {"history": {"columns": list(COLUMNS), "data": rows}}
    if cursor:
        payload["history.cursor"] = {
            "columns": ["INDEX", "TOTAL", "PAGESIZE"],
            "data": [[index, index + len(rows) if total is None else total, page_size]],
        }
    return payload


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
        httpx.Response(200, json=_page([_row("SBER"), _row("GAZP")], total=3)),
        httpx.Response(200, json=_page([_row("LKOH")], index=2, total=3)),
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
async def test_server_page_cap_does_not_truncate_requested_limit() -> None:
    config = IssConfig(
        base_url=BASE,
        page_limit=1000,
        retries=1,
        timeout_seconds=1.0,
        initial_retry_delay_seconds=0.0,
    )
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(
            200,
            json=_page([_row("SBER"), _row("GAZP")], total=3, page_size=2),
        ),
        httpx.Response(
            200,
            json=_page([_row("LKOH")], index=2, total=3, page_size=2),
        ),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert [row["SECID"] for row in rows] == ["SBER", "GAZP", "LKOH"]
    assert [call.request.url.params["start"] for call in route.calls] == ["0", "2"]


@respx.mock
async def test_analytics_uses_its_cursor_when_server_caps_page() -> None:
    config = IssConfig(
        base_url=BASE,
        page_limit=1000,
        retries=1,
        timeout_seconds=1.0,
        initial_retry_delay_seconds=0.0,
    )

    def analytics_page(rows: list[list[object]], index: int, total: int) -> dict[str, object]:
        return {
            "analytics": {
                "columns": ["ticker", "weight", "tradedate"],
                "data": rows,
            },
            "analytics.cursor": {
                "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                "data": [[index, total, 2]],
            },
        }

    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(
            200,
            json=analytics_page(
                [["SBER", "30.1", "2026-08-28"], ["GAZP", "20.2", "2026-08-28"]],
                0,
                3,
            ),
        ),
        httpx.Response(
            200,
            json=analytics_page([["LKOH", "10.3", "2026-08-28"]], 2, 3),
        ),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_index_analytics("IMOEX", "2026-08-28")
    assert [row["ticker"] for row in rows] == ["SBER", "GAZP", "LKOH"]
    assert [call.request.url.params["start"] for call in route.calls] == ["0", "2"]


@respx.mock
async def test_full_last_page_finishes_by_cursor_total(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(200, json=_page([_row("SBER"), _row("GAZP")], total=4)),
        httpx.Response(
            200,
            json=_page([_row("LKOH"), _row("ROSN")], index=2, total=4),
        ),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert len(rows) == 4
    assert route.call_count == 2


@respx.mock
async def test_without_cursor_walks_until_empty_page(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(200, json=_page([_row("SBER")], cursor=False)),
        httpx.Response(200, json=_page([], cursor=False)),
    ]
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert [row["SECID"] for row in rows] == ["SBER"]
    assert [call.request.url.params["start"] for call in route.calls] == ["0", "1"]


@respx.mock
async def test_repeated_page_without_cursor_is_error(config: IssConfig) -> None:
    repeated = _page([_row("SBER")], cursor=False)
    route = respx.get(url__startswith=BASE)
    route.side_effect = [httpx.Response(200, json=repeated), httpx.Response(200, json=repeated)]
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="повторил страницу"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
async def test_late_page_error_does_not_return_partial_result(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE)
    route.side_effect = [
        httpx.Response(200, json=_page([_row("SBER"), _row("GAZP")], total=3)),
        httpx.Response(404),
    ]
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="404"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
async def test_budget_is_checked_between_pages(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_page([_row("SBER")], total=2))
    )
    permits = 0

    async def permit() -> None:
        nonlocal permits
        permits += 1
        if permits > 1:
            raise SourceStoppedError(detail="request_budget_exhausted")

    async with IssClient(config, request_permit=permit) as client:
        with pytest.raises(SourceStoppedError, match="request_budget_exhausted"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert route.call_count == 1


@respx.mock
async def test_stop_is_checked_between_pages(config: IssConfig) -> None:
    route = respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_page([_row("SBER")], total=2))
    )
    stop = False

    def should_stop() -> bool:
        nonlocal stop
        if route.call_count:
            stop = True
        return stop

    async with IssClient(config, should_stop=should_stop) as client:
        with pytest.raises(SourceStoppedError):
            await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert route.call_count == 1


@respx.mock
async def test_unexpected_block_is_not_treated_as_empty(config: IssConfig) -> None:
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json={"unexpected_block": {"message": "not history"}})
    )
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="не содержит блок 'history'"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
async def test_missing_requested_column_is_contract_error(config: IssConfig) -> None:
    columns = [column for column in COLUMNS if column != "CLOSE"]
    row = [value for position, value in enumerate(_row("SBER")) if COLUMNS[position] != "CLOSE"]
    payload = {"history": {"columns": columns, "data": [row]}}
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=payload))
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="обязательные колонки CLOSE"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
async def test_null_value_in_present_column_is_valid(config: IssConfig) -> None:
    row = _row("SBER")
    row[COLUMNS.index("CLOSE")] = None
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=_page([row])))
    async with IssClient(config) as client:
        rows = await client.fetch_session_rows("2026-08-28", COLUMNS)
    assert rows[0]["CLOSE"] is None


@respx.mock
async def test_malformed_row_is_contract_error(config: IssConfig) -> None:
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_page([_row("SBER")[:-1]]))
    )
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="6 значений для 7 колонок"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
async def test_foreign_session_date_is_contract_error(config: IssConfig) -> None:
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=_page([_row("SBER", "2026-08-27")]))
    )
    async with IssClient(config) as client:
        with pytest.raises(IssError, match="чужой дате 2026-08-27"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)


@respx.mock
@pytest.mark.parametrize(
    ("method", "args", "block", "columns", "missing"),
    [
        (
            "fetch_futures_series",
            (),
            "series",
            ["underlying_asset", "asset_code"],
            "secid",
        ),
        (
            "fetch_futures_open_interest",
            (),
            "securities",
            ["ASSETCODE"],
            "PREVOPENPOSITION",
        ),
        ("fetch_equity_lot_sizes", (), "securities", ["SECID"], "LOTSIZE"),
        ("fetch_equity_isins", (), "securities", ["SECID"], "ISIN"),
        ("fetch_emitter_id", ("SBER",), "description", ["name"], "value"),
        ("fetch_index_analytics", ("IMOEX",), "analytics", ["ticker", "weight"], "tradedate"),
        ("fetch_index_titles", (), "indices", ["indexid"], "shortname"),
    ],
)
async def test_reference_blocks_require_consumer_columns(
    config: IssConfig,
    method: str,
    args: tuple[str, ...],
    block: str,
    columns: list[str],
    missing: str,
) -> None:
    respx.get(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json={block: {"columns": columns, "data": []}})
    )
    async with IssClient(config) as client:
        call = getattr(client, method)
        with pytest.raises(IssError, match=f"обязательные колонки {missing}"):
            await call(*args)


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
    assert client.metrics.to_dict() == {
        "attempts": 2,
        "successful_responses": 1,
        "retries": 1,
        "search_probes": 0,
        "elapsed_seconds": pytest.approx(client.metrics.elapsed_seconds),
    }


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
async def test_request_permit_stops_before_a_retry(config: IssConfig) -> None:
    """A repair budget is consumed per physical attempt, not per source."""
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(503))
    permits = 0

    async def permit() -> None:
        nonlocal permits
        permits += 1
        if permits > 1:
            raise SourceStoppedError(detail="request_budget_exhausted")

    async with IssClient(config, request_permit=permit) as client:
        with pytest.raises(SourceStoppedError, match="request_budget_exhausted"):
            await client.fetch_session_rows("2026-08-28", COLUMNS)

    assert route.call_count == 1
    assert permits == 2


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
