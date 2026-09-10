"""Фикстуры тестов сбора рыночных данных.

Ключевое: **ни один тест не должен ходить в настоящую сеть.** Клиент Банка
России подменяется здесь — иначе прогон упирается в таймауты `cbr.ru`, и
набор тестов из шестисекундного превращается в двухминутный.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import httpx
import pytest

from financial_ai.market_data.sources import cbr
from financial_ai.market_data.sources.positions_client import (
    PositionSnapshot,
    PositionsSourceError,
)

KEY_RATE_HTML = """
<table class="data">
  <tr><th>Дата</th><th>Ставка</th></tr>
  <tr><td>28.08.2026</td><td>16,50</td></tr>
</table>
"""

# Страница ЦБ отдаёт кривую доходности по ДВЕНАДЦАТИ срокам внутри
# `.table-wrapper`, и колонки читаются по позиции, а не по тексту заголовка —
# так делает оригинал (`cbr_zcyc_params_sync/cli.py:11-23`). Прежний образец
# изображал таблицу параметров модели, закрепляя дефект.
ZCYC_HTML = """
<div class="table-wrapper">
<table>
  <tr><th>Дата</th><th>0,25</th><th>0,5</th><th>0,75</th><th>1</th><th>2</th><th>3</th><th>5</th><th>7</th><th>10</th><th>15</th><th>20</th><th>30</th></tr>
  <tr><td>28.08.2026</td><td>16,10</td><td>16,05</td><td>16,00</td><td>15,80</td><td>14,90</td><td>14,20</td><td>13,50</td><td>13,10</td><td>12,80</td><td>12,50</td><td>12,30</td><td>12,10</td></tr>
  <tr><td>31.08.2026</td><td>16,10</td><td>16,05</td><td>16,00</td><td>15,80</td><td>14,90</td><td>14,20</td><td>13,50</td><td>13,10</td><td>12,80</td><td>12,50</td><td>12,30</td><td>12,10</td></tr>
</table>
</div>
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


# --- источник позиций (spec 005) ---------------------------------------------
#
# Позиции ходят не в биржевой интерфейс данных, а формой на сайт биржи, поэтому
# подделывать нужно отдельный клиент. Подделка держит те же обещания, что и
# настоящий клиент: снимок за другую дату не выдаётся, отсутствие данных — это
# `None`, а не пустой снимок.


@dataclass
class FakeSnapshot:
    """Готовые значения для одной пары «контракт — дата»."""

    fiz_long: Decimal | None = Decimal("100")
    fiz_short: Decimal | None = Decimal("50")
    jur_long: Decimal | None = Decimal("70")
    jur_short: Decimal | None = Decimal("30")


class FakePositionsClient:
    """Подделка источника позиций.

    ``available`` — какие пары «контракт — дата» источник знает. Пары вне его
    дают ``None``: именно так настоящий источник сообщает, что данных нет.
    """

    def __init__(
        self,
        contracts: dict[str, str] | None = None,
        available: dict[tuple[str, object], FakeSnapshot] | None = None,
        empty: bool = False,
        first_available: dict[str, object] | None = None,
    ) -> None:
        self.contract_map = contracts if contracts is not None else {"SBER": "SBRF_F"}
        self.available = available
        self.empty = empty
        self.first_available = first_available or {}
        self.calls: list[tuple[str, object]] = []
        self.discoveries: list[str] = []
        self.failures = 0
        self.fail_until = 0

    async def __aenter__(self) -> FakePositionsClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def contracts(self, iss: object) -> dict[str, str]:
        return self.contract_map

    def searched_for_first_date(self, contract_code: str) -> bool:
        return contract_code in self.discoveries

    async def first_available_date(
        self, contract_code: str, sessions: list[object]
    ) -> object | None:
        """Ищет так же, как настоящий клиент: по наличию данных в пробах."""
        if not sessions:
            return None
        self.discoveries.append(contract_code)
        if contract_code in self.first_available:
            return self.first_available[contract_code]
        if self.available is not None:
            days = sorted(day for code, day in self.available if code == contract_code)  # type: ignore[type-var]
            return days[0] if days else None
        return sessions[0]

    async def fetch(self, contract_code: str, day: object) -> object | None:
        self.calls.append((contract_code, day))
        if self.fail_until and len(self.calls) < self.fail_until:
            raise PositionsSourceError("источник недоступен")
        if self.empty:
            return None
        if self.available is not None and (contract_code, day) not in self.available:
            return None
        values = (
            self.available[(contract_code, day)] if self.available is not None else FakeSnapshot()
        )
        return PositionSnapshot(
            trade_date=day,  # type: ignore[arg-type]
            fiz_long=values.fiz_long,
            fiz_short=values.fiz_short,
            jur_long=values.jur_long,
            jur_short=values.jur_short,
        )


@pytest.fixture
def positions_client() -> FakePositionsClient:
    """Подделка источника позиций со значениями по SBER."""
    return FakePositionsClient()
