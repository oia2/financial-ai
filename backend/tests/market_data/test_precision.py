"""Тесты точности цен.

Значение, полученное от биржи, должно дойти до хранилища без искажений.
`float` на этом пути запрещён: пройдя через него, значение уже не восстановить.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx

from financial_ai.market_data.iss.client import IssClient, IssConfig, ResponseContractError
from financial_ai.market_data.sources.equity_d1 import rows_to_bars, to_decimal

SESSION = dt.date(2026, 8, 28)

PRECISION_BASE = "https://iss.moex.com/iss"


def _precision_config() -> IssConfig:
    return IssConfig(
        base_url=PRECISION_BASE,
        page_limit=2,
        retries=1,
        timeout_seconds=1.0,
        initial_retry_delay_seconds=0.0,
    )


@pytest.mark.parametrize(
    "raw",
    ["312.4", "0.000000001", "123456789.123456789", "1e-9", "-15.75", "0"],
)
def test_decimal_roundtrip_is_exact(raw: str) -> None:
    parsed = to_decimal(raw)
    assert parsed == Decimal(raw)


def test_nine_decimal_places_survive() -> None:
    """NUMERIC(28,9): девять знаков после запятой должны сохраниться."""
    value = to_decimal("0.123456789")
    assert value is not None
    assert str(value) == "0.123456789"


def test_float_input_does_not_lose_precision_silently() -> None:
    """Даже если биржа пришлёт число, разбор идёт через строку.

    `Decimal(0.1)` дало бы 0.1000000000000000055511151231257827, а
    `Decimal(str(0.1))` — ровно 0.1.
    """
    assert to_decimal(0.1) == Decimal("0.1")


def test_missing_value_is_none_not_zero() -> None:
    """Отсутствие наблюдения и нулевая цена — разные факты."""
    assert to_decimal(None) is None
    assert to_decimal("") is None
    assert to_decimal("0") == Decimal("0")
    assert to_decimal("0") is not None


@pytest.mark.parametrize("raw", ["н/д", "broken-value", "NaN", "Infinity", True])
def test_unparsable_value_is_contract_violation(raw: object) -> None:
    """Нераспознанное число — не законный пропуск (FR-032e).

    Прежде оно становилось ``None`` и получало доказательство полноты наравне
    с настоящим отсутствием значения.
    """
    with pytest.raises(ResponseContractError):
        to_decimal(raw)


def test_bars_carry_exact_prices() -> None:
    rows = [
        {
            "SECID": "SBER",
            "OPEN": "312.400000001",
            "HIGH": "315.1",
            "LOW": "311.05",
            "CLOSE": "314.22",
            "VOLUME": "12345678",
        }
    ]
    bars = rows_to_bars(rows, SESSION)
    assert bars[0].open == Decimal("312.400000001")
    assert bars[0].close == Decimal("314.22")


def test_no_float_on_the_parsing_path() -> None:
    """Сторожевой тест: тип значения — Decimal, а не float."""
    bars = rows_to_bars([{"SECID": "SBER", "CLOSE": "314.22"}], SESSION)
    assert isinstance(bars[0].close, Decimal)
    assert not isinstance(bars[0].close, float)


# --- Разбор ответа биржи -----------------------------------------------------
#
# Тесты выше подают значения СТРОКАМИ, и на строках потери нет никогда. Ответы
# ISS приходят числами JSON, а `response.json()` разбирал их в `float`: на
# стенде 2026-09-20 `123456789.123456789` доходило до хранилища как
# `123456789.12345679`. Дефект жил рядом с этими тестами, потому что HTTP-путь
# ни один из них не проходил (T204).


def _with_single_page_cursor(body: bytes) -> bytes:
    return (
        body[:-1]
        + b', "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"],'
        + b' "data": [[0, 1, 100]]}}'
    )


@respx.mock
async def test_json_numbers_survive_http_response() -> None:
    """Число JSON без кавычек доходит из ответа биржи точным."""
    body = _with_single_page_cursor(
        b'{"history": {"columns": ["SECID", "OPEN", "CLOSE", "VOLUME"],'
        b' "data": [["SBER", 123456789.123456789, 1e-9, 0]]}}'
    )
    respx.get(url__startswith=PRECISION_BASE).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )
    async with IssClient(_precision_config()) as client:
        rows = await client.fetch_session_rows("2026-08-28", ("SECID", "OPEN", "CLOSE", "VOLUME"))

    assert rows[0]["OPEN"] == Decimal("123456789.123456789")
    assert rows[0]["CLOSE"] == Decimal("1E-9")
    assert rows[0]["VOLUME"] == 0


@respx.mock
async def test_json_numbers_are_not_float_after_parsing() -> None:
    """Сторожевой тест пути: дробное число ответа — Decimal, а не float."""
    body = _with_single_page_cursor(
        b'{"history": {"columns": ["SECID", "CLOSE"], "data": [["SBER", 314.22]]}}'
    )
    respx.get(url__startswith=PRECISION_BASE).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )
    async with IssClient(_precision_config()) as client:
        rows = await client.fetch_session_rows("2026-08-28", ("SECID", "CLOSE"))

    assert isinstance(rows[0]["CLOSE"], Decimal)
    assert not isinstance(rows[0]["CLOSE"], float)


@respx.mock
async def test_json_null_stays_missing_not_zero() -> None:
    """`null` ответа остаётся отсутствием наблюдения и нулём не становится."""
    body = _with_single_page_cursor(
        b'{"history": {"columns": ["SECID", "OPEN", "CLOSE"], "data": [["SBER", null, 314.22]]}}'
    )
    respx.get(url__startswith=PRECISION_BASE).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )
    async with IssClient(_precision_config()) as client:
        rows = await client.fetch_session_rows("2026-08-28", ("SECID", "OPEN", "CLOSE"))

    bars = rows_to_bars(rows, SESSION)
    assert bars[0].open is None
    assert bars[0].close == Decimal("314.22")


@respx.mock
async def test_exact_value_reaches_the_bar_from_http() -> None:
    """Путь «ответ HTTP → домен» целиком: значение доходит без искажения."""
    body = _with_single_page_cursor(
        b'{"history": {"columns": ["SECID", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"],'
        b' "data": [["SBER", 312.400000001, 315.1, 311.05, 123456789.123456789, 12345678]]}}'
    )
    respx.get(url__startswith=PRECISION_BASE).mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )
    async with IssClient(_precision_config()) as client:
        rows = await client.fetch_session_rows(
            "2026-08-28", ("SECID", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")
        )

    bars = rows_to_bars(rows, SESSION)
    assert bars[0].open == Decimal("312.400000001")
    assert bars[0].close == Decimal("123456789.123456789")
    # Через float закрытие стало бы 123456789.12345679 — разница в девятом знаке,
    # ровно там, где у NUMERIC(28,9) кончается точность.
    assert str(bars[0].close) == "123456789.123456789"
