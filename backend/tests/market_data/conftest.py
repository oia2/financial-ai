"""Фикстуры тестов сбора рыночных данных.

Ключевое: **ни один тест не должен ходить в настоящую сеть.** Клиент Банка
России подменяется здесь — иначе прогон упирается в таймауты `cbr.ru`, и
набор тестов из шестисекундного превращается в двухминутный.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from financial_ai.market_data.sources import cbr

KEY_RATE_HTML = """
<table class="data">
  <tr><th>Дата</th><th>Ставка</th></tr>
  <tr><td>28.08.2026</td><td>16,50</td></tr>
</table>
"""

ZCYC_HTML = """
<table>
  <tr><th>Дата</th><th>B1</th></tr>
  <tr><td>28.08.2026</td><td>7,1234</td></tr>
</table>
"""


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if cbr.ZCYC_URL in url:
        return httpx.Response(200, text=ZCYC_HTML)
    if cbr.KEY_RATE_URL in url:
        return httpx.Response(200, text=KEY_RATE_HTML)
    return httpx.Response(404, text="не тот адрес")


@pytest.fixture
def cbr_client() -> Iterator[httpx.AsyncClient]:
    """Подделка Банка России. Сеть не задействуется."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    yield client
