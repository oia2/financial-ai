"""Дневные макроряды Банка России.

Это **не MOEX**: ЦБ отдаёт HTML-страницы, и значения приходится извлекать из
таблицы. Разбор перенесён из `pipelines/cbr_key_rate_sync/cli.py` и
`pipelines/cbr_zcyc_params_sync/cli.py` (`MR-MASTER-DRO`, `f07295e`) — селекторы,
формат даты и правила очистки числа взяты оттуда, а не подобраны заново.

Режим доступности у этих рядов иной, чем у рыночных: по
`docs/specs/time_semantics.md` они публикуются **до** закрытия сессии и уже
относятся к текущему дню `t`. Рыночные ряды, наоборот, доступны только после
закрытия.

> **Ограничение, о котором нужно знать.** Разбор чужой HTML-страницы проверен
> структурно — на образце той формы, которую ожидает исходный код. Сверка с
> живой страницей `cbr.ru` не выполнялась: она требует доступа к сети. Перед
> боевым использованием разбор нужно проверить на настоящем ответе.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from financial_ai.market_data.sources.equity_d1 import to_decimal

logger = logging.getLogger(__name__)

SOURCE_ID = "cbr"

KEY_RATE_URL = "https://cbr.ru/hd_base/KeyRate/"
ZCYC_URL = "https://cbr.ru/hd_base/zcyc_params/"

KEY_RATE_SERIES_ID = "CBR_KEY_RATE"
ZCYC_SERIES_PREFIX = "CBR_ZCYC_"

CBR_DATE_FORMAT = "%d.%m.%Y"


class CbrError(RuntimeError):
    """Обращение к Банку России не удалось."""


@dataclass(frozen=True, slots=True)
class CbrConfig:
    timeout_seconds: float = 60.0


async def fetch_key_rate(
    config: CbrConfig,
    date_from: dt.date,
    date_till: dt.date,
    client: httpx.AsyncClient | None = None,
) -> dict[dt.date, Decimal | None]:
    """Ключевая ставка за период."""
    html = await _get(
        KEY_RATE_URL,
        {
            "UniDbQuery.Posted": "True",
            "UniDbQuery.From": date_from.strftime(CBR_DATE_FORMAT),
            "UniDbQuery.To": date_till.strftime(CBR_DATE_FORMAT),
        },
        config,
        client,
    )
    return parse_key_rate_html(html)


def parse_key_rate_html(html: str) -> dict[dt.date, Decimal | None]:
    """Разобрать таблицу ключевой ставки.

    Ожидается `<table class="data">` со строками из двух ячеек: дата и ставка.
    Селектор и формат — из исходного пайплайна.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="data")
    if table is None:
        raise CbrError("таблица ключевой ставки не найдена в ответе ЦБ")

    values: dict[dt.date, Decimal | None] = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        day = _parse_cbr_date(cells[0].get_text(strip=True))
        if day is None:
            continue
        values[day] = to_decimal(_clean_number(cells[1].get_text(strip=True)))
    return values


async def fetch_zcyc(
    config: CbrConfig,
    date_from: dt.date,
    date_till: dt.date,
    client: httpx.AsyncClient | None = None,
) -> dict[str, dict[dt.date, Decimal | None]]:
    """Параметры кривой бескупонной доходности за период.

    Возвращает по одному ряду на параметр: заголовок колонки становится
    суффиксом идентификатора ряда.
    """
    html = await _get(
        ZCYC_URL,
        {
            "UniDbQuery.Posted": "True",
            "UniDbQuery.From": date_from.strftime(CBR_DATE_FORMAT),
            "UniDbQuery.To": date_till.strftime(CBR_DATE_FORMAT),
        },
        config,
        client,
    )
    return parse_zcyc_html(html)


def parse_zcyc_html(html: str) -> dict[str, dict[dt.date, Decimal | None]]:
    """Разобрать таблицу параметров ЗКЦ.

    Первая колонка — дата, остальные — параметры кривой. Имена параметров
    берутся из заголовка таблицы.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise CbrError("таблица параметров ЗКЦ не найдена в ответе ЦБ")

    rows = table.find_all("tr")
    if not rows:
        raise CbrError("таблица параметров ЗКЦ пуста")

    headers = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])]
    if len(headers) < 2:
        raise CbrError("в таблице параметров ЗКЦ нет колонок со значениями")

    series: dict[str, dict[dt.date, Decimal | None]] = {
        f"{ZCYC_SERIES_PREFIX}{_normalise(name)}": {} for name in headers[1:]
    }

    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) != len(headers):
            continue
        day = _parse_cbr_date(cells[0].get_text(strip=True))
        if day is None:
            continue
        for position, name in enumerate(headers[1:], start=1):
            key = f"{ZCYC_SERIES_PREFIX}{_normalise(name)}"
            series[key][day] = to_decimal(_clean_number(cells[position].get_text(strip=True)))

    return series


async def _get(
    url: str, params: dict[str, str], config: CbrConfig, client: httpx.AsyncClient | None
) -> str:
    owns = client is None
    http = client or httpx.AsyncClient(timeout=config.timeout_seconds)
    try:
        response = await http.get(url, params=params)
    except httpx.HTTPError as error:
        raise CbrError(f"ЦБ недоступен: {error}") from error
    finally:
        if owns:
            await http.aclose()

    if response.status_code != httpx.codes.OK:
        raise CbrError(f"ЦБ ответил {response.status_code} на {url}")
    return response.text


def _parse_cbr_date(raw: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(raw, CBR_DATE_FORMAT).date()
    except ValueError:
        return None


def _clean_number(raw: str) -> str:
    """Русский формат числа: пробелы-разделители и запятая вместо точки."""
    return raw.replace("\xa0", "").replace(" ", "").replace(",", ".")


def _normalise(name: str) -> str:
    """Имя параметра в идентификатор ряда."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.strip())
    return cleaned.strip("_").upper() or "UNNAMED"
