"""Тесты разбора ответов Банка России.

Образцы HTML построены по структуре, которую ожидает исходный код
`MR-MASTER-DRO` (`table.data`, строки из двух ячеек, дата `%d.%m.%Y`, число с
запятой и неразрывными пробелами). Это проверяет перенос, но **не** заменяет
сверку с живой страницей `cbr.ru` — её нужно выполнить перед боевым запуском.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx

from financial_ai.market_data.sources import cbr

KEY_RATE_HTML = """
<html><body>
<table class="data">
  <thead><tr><th>Дата</th><th>Ставка</th></tr></thead>
  <tbody>
    <tr><td>28.08.2026</td><td>16,50</td></tr>
    <tr><td>27.08.2026</td><td>16,50</td></tr>
    <tr><td>26.08.2026</td><td>17,00</td></tr>
  </tbody>
</table>
</body></html>
"""

ZCYC_HTML = """
<html><body>
<table>
  <tr><th>Дата</th><th>B1</th><th>B2</th><th>0,25</th></tr>
  <tr><td>28.08.2026</td><td>7,1234</td><td>-1,5</td><td>15,25</td></tr>
  <tr><td>27.08.2026</td><td>7,0000</td><td>-1,4</td><td>15,10</td></tr>
</table>
</body></html>
"""


# --- ключевая ставка ---------------------------------------------------------


def test_key_rate_is_parsed() -> None:
    values = cbr.parse_key_rate_html(KEY_RATE_HTML)
    assert values[dt.date(2026, 8, 28)] == Decimal("16.50")
    assert values[dt.date(2026, 8, 26)] == Decimal("17.00")


def test_key_rate_comma_becomes_dot() -> None:
    """Русский формат числа: запятая — десятичный разделитель."""
    values = cbr.parse_key_rate_html(KEY_RATE_HTML)
    assert values[dt.date(2026, 8, 28)] == Decimal("16.5")


def test_key_rate_handles_nbsp_thousands() -> None:
    """ЦБ разделяет разряды неразрывным пробелом — он должен исчезнуть."""
    html = '<table class="data"><tr><td>28.08.2026</td><td>1\xa0234,56</td></tr></table>'
    assert cbr.parse_key_rate_html(html)[dt.date(2026, 8, 28)] == Decimal("1234.56")


def test_key_rate_skips_header_and_malformed_rows() -> None:
    """Строки не из двух ячеек и нечитаемые даты пропускаются молча."""
    html = """
    <table class="data">
      <tr><th>Дата</th><th>Ставка</th></tr>
      <tr><td>не дата</td><td>16,5</td></tr>
      <tr><td>28.08.2026</td><td>16,5</td><td>лишняя</td></tr>
      <tr><td>28.08.2026</td><td>16,5</td></tr>
    </table>
    """
    values = cbr.parse_key_rate_html(html)
    assert list(values) == [dt.date(2026, 8, 28)]


def test_missing_table_is_reported() -> None:
    """Молчаливый пустой результат хуже отказа: страница могла измениться."""
    with pytest.raises(cbr.CbrError, match="не найдена"):
        cbr.parse_key_rate_html("<html><body>ничего нет</body></html>")


def test_empty_value_stays_none_not_zero() -> None:
    html = '<table class="data"><tr><td>28.08.2026</td><td></td></tr></table>'
    assert cbr.parse_key_rate_html(html)[dt.date(2026, 8, 28)] is None


# --- ЗКЦ ---------------------------------------------------------------------


def test_zcyc_splits_columns_into_series() -> None:
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    assert set(series) == {"CBR_ZCYC_B1", "CBR_ZCYC_B2", "CBR_ZCYC_0_25"}


def test_zcyc_values_are_parsed() -> None:
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    assert series["CBR_ZCYC_B1"][dt.date(2026, 8, 28)] == Decimal("7.1234")
    assert series["CBR_ZCYC_B2"][dt.date(2026, 8, 28)] == Decimal("-1.5")


def test_zcyc_missing_table_is_reported() -> None:
    with pytest.raises(cbr.CbrError, match="не найдена"):
        cbr.parse_zcyc_html("<html><body></body></html>")


def test_zcyc_without_value_columns_is_reported() -> None:
    with pytest.raises(cbr.CbrError, match="нет колонок"):
        cbr.parse_zcyc_html("<table><tr><th>Дата</th></tr></table>")


# --- обращение ---------------------------------------------------------------


@respx.mock
async def test_key_rate_is_fetched() -> None:
    route = respx.get(cbr.KEY_RATE_URL).mock(return_value=httpx.Response(200, text=KEY_RATE_HTML))
    values = await cbr.fetch_key_rate(cbr.CbrConfig(), dt.date(2026, 8, 26), dt.date(2026, 8, 28))
    assert len(values) == 3
    # Формат даты в параметрах — тот, который понимает ЦБ.
    assert "26.08.2026" in str(route.calls[0].request.url)


@respx.mock
async def test_unavailable_cbr_is_reported() -> None:
    respx.get(cbr.KEY_RATE_URL).mock(side_effect=httpx.ConnectError("нет связи"))
    with pytest.raises(cbr.CbrError, match="недоступен"):
        await cbr.fetch_key_rate(cbr.CbrConfig(), dt.date(2026, 8, 26), dt.date(2026, 8, 28))


@respx.mock
async def test_error_status_is_reported() -> None:
    respx.get(cbr.ZCYC_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(cbr.CbrError, match="503"):
        await cbr.fetch_zcyc(cbr.CbrConfig(), dt.date(2026, 8, 26), dt.date(2026, 8, 28))
