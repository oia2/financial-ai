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

# Настоящая страница ЦБ: кривая доходности по двенадцати срокам внутри
# `.table-wrapper`. Колонки читаются ПО ПОЗИЦИИ — так делает оригинал, и
# полагаться на текст заголовка нельзя.
ZCYC_HTML = """
<html><body>
<div class="table-wrapper">
<table>
  <tr><th>Дата</th><th>0,25</th><th>0,5</th><th>0,75</th><th>1</th><th>2</th><th>3</th><th>5</th><th>7</th><th>10</th><th>15</th><th>20</th><th>30</th></tr>
  <tr><td>28.08.2026</td><td>16,10</td><td>16,05</td><td>16,00</td><td>15,80</td><td>14,90</td><td>14,20</td><td>13,50</td><td>13,10</td><td>12,80</td><td>12,50</td><td>12,30</td><td>12,10</td></tr>
  <tr><td>27.08.2026</td><td>16,20</td><td>16,15</td><td>16,10</td><td>15,90</td><td>15,00</td><td>14,30</td><td>13,60</td><td>13,20</td><td>12,90</td><td>12,60</td><td>12,40</td><td>12,20</td></tr>
</table>
</div>
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


def test_zcyc_yields_points_not_model_parameters() -> None:
    """Модели нужны точки кривой, а не параметры Нельсона-Сигеля.

    `global_regime_core_v1.yaml` объявляет `yield_curve_points`, а из
    параметров их без формулы не получить. Прежняя версия сохраняла `B1` и
    подобное — и тест это закреплял.
    """
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    assert {"CBR_ZCYC_yield_1y", "CBR_ZCYC_yield_2y"} <= set(series)
    assert not any(name.endswith("_B1") for name in series)


def test_zcyc_covers_every_term_of_the_original() -> None:
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    assert set(series) == {f"CBR_ZCYC_{term}" for term in cbr.ZCYC_TERMS}


def test_zcyc_points_declared_by_the_feature_config_are_present() -> None:
    """Ровно те четыре точки, по которым считается наклон кривой."""
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    for point in ("yield_1y", "yield_2y", "yield_5y", "yield_10y"):
        assert series[f"CBR_ZCYC_{point}"][dt.date(2026, 8, 28)] is not None


def test_zcyc_values_are_parsed() -> None:
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    assert series["CBR_ZCYC_yield_0_25y"][dt.date(2026, 8, 28)] == Decimal("16.10")
    assert series["CBR_ZCYC_yield_30y"][dt.date(2026, 8, 28)] == Decimal("12.10")


def test_zcyc_columns_are_read_by_position() -> None:
    """Заголовки на странице — сроки, а не имена рядов."""
    series = cbr.parse_zcyc_html(ZCYC_HTML)
    assert series["CBR_ZCYC_yield_1y"][dt.date(2026, 8, 27)] == Decimal("15.90")


def test_zcyc_wrong_table_shape_is_reported() -> None:
    """Разметка страницы изменилась — это неуспех, а не пустой результат."""
    html = (
        '<div class="table-wrapper"><table><tr><td>28.08.2026</td><td>7,1</td></tr></table></div>'
    )
    with pytest.raises(cbr.CbrError, match="сроками"):
        cbr.parse_zcyc_html(html)


def test_zcyc_missing_table_is_reported() -> None:
    with pytest.raises(cbr.CbrError, match="не найдена"):
        cbr.parse_zcyc_html("<html><body></body></html>")


def test_zcyc_outside_the_wrapper_is_not_read() -> None:
    """Таблица берётся из `.table-wrapper`, а не первая на странице.

    На странице ЦБ таблиц несколько, и прежняя версия читала не ту — отсюда
    параметры модели вместо точек кривой.
    """
    with pytest.raises(cbr.CbrError, match="не найдена"):
        cbr.parse_zcyc_html("<table><tr><th>Дата</th><th>B1</th></tr></table>")


# --- обращение ---------------------------------------------------------------


@respx.mock
async def test_key_rate_is_fetched() -> None:
    route = respx.get(cbr.KEY_RATE_URL).mock(return_value=httpx.Response(200, text=KEY_RATE_HTML))
    values = await cbr.fetch_key_rate(
        cbr.CbrConfig(retry_backoff_seconds=0.0), dt.date(2026, 8, 26), dt.date(2026, 8, 28)
    )
    assert len(values) == 3
    # Формат даты в параметрах — тот, который понимает ЦБ.
    assert "26.08.2026" in str(route.calls[0].request.url)


@respx.mock
async def test_unavailable_cbr_is_reported() -> None:
    respx.get(cbr.KEY_RATE_URL).mock(side_effect=httpx.ConnectError("нет связи"))
    with pytest.raises(cbr.CbrError, match="недоступен"):
        await cbr.fetch_key_rate(
            cbr.CbrConfig(retry_backoff_seconds=0.0), dt.date(2026, 8, 26), dt.date(2026, 8, 28)
        )


@respx.mock
async def test_error_status_is_reported() -> None:
    respx.get(cbr.ZCYC_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(cbr.CbrError, match="503"):
        await cbr.fetch_zcyc(
            cbr.CbrConfig(retry_backoff_seconds=0.0), dt.date(2026, 8, 26), dt.date(2026, 8, 28)
        )


@respx.mock
async def test_оборванное_соединение_повторяется() -> None:
    """Сайт ЦБ роняет часть соединений (T106).

    На стенде 2026-09-19 — 54 неуспеха на 118 успехов, почти треть, при нуле
    неуспехов у остальных источников. Повторов у ЦБ не было вовсе, и каждый
    обрыв стоил всей сессии: она оставалась недобранной и собиралась заново
    целиком, вместе с прочими источниками.
    """
    route = respx.get(cbr.KEY_RATE_URL).mock(
        side_effect=[
            httpx.ConnectError("нет связи"),
            httpx.Response(200, text=KEY_RATE_HTML),
        ]
    )

    config = cbr.CbrConfig(retry_backoff_seconds=0.0)
    rates = await cbr.fetch_key_rate(config, dt.date(2026, 9, 2), dt.date(2026, 9, 2))

    assert route.call_count == 2
    assert rates


@respx.mock
async def test_причина_названа_даже_когда_обрыв_молчит() -> None:
    """У обрыва соединения текст пустой.

    Без имени класса в сообщении оставалось «ЦБ недоступен: » — причина, по
    которой нечего искать.
    """
    respx.get(cbr.KEY_RATE_URL).mock(side_effect=httpx.ConnectError(""))

    config = cbr.CbrConfig(retries=1, retry_backoff_seconds=0.0)
    with pytest.raises(cbr.CbrError, match="ConnectError"):
        await cbr.fetch_key_rate(config, dt.date(2026, 9, 2), dt.date(2026, 9, 2))
