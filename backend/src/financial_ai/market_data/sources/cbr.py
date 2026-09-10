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

# Сроки кривой бескупонной доходности в порядке колонок таблицы ЦБ.
# Перенесено дословно из `pipelines/cbr_zcyc_params_sync/cli.py:11-23`:
# оригинал отображает колонки ПО ПОЗИЦИИ, а не по тексту заголовка.
#
# Прежняя версия называла ряды по заголовку и брала первую таблицу страницы —
# из-за этого в хранилище оседали параметры модели Нельсона-Сигеля (`B1` и
# подобные) вместо точек кривой. Модели нужны именно точки: конфигурация
# признаков объявляет `yield_curve_points: [yield_1y, yield_2y, yield_5y,
# yield_10y]`, а из параметров их без формулы не получить.
ZCYC_TERMS: tuple[str, ...] = (
    "yield_0_25y",
    "yield_0_5y",
    "yield_0_75y",
    "yield_1y",
    "yield_2y",
    "yield_3y",
    "yield_5y",
    "yield_7y",
    "yield_10y",
    "yield_15y",
    "yield_20y",
    "yield_30y",
)

# Точки, которые конфигурация признаков модели объявляет входом
# (`yield_curve_points`). Их отсутствие — отказ, а не неполнота: без них
# `data_plane_step6` не считает наклон кривой.
REQUIRED_ZCYC_TERMS: tuple[str, ...] = ("yield_1y", "yield_2y", "yield_5y", "yield_10y")

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
    """Разобрать таблицу точек кривой бескупонной доходности.

    Первая колонка — дата, остальные двенадцать — доходности по срокам.
    Отображение **по позиции**, как в оригинале: заголовки на странице ЦБ
    подписаны сроками, а не именами рядов, и полагаться на их текст нельзя.

    Таблица берётся из `.table-wrapper`, а не первая на странице: на странице
    есть и другие, и прежняя версия читала не ту.
    """
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.select_one(".table-wrapper")
    table = wrapper.find("table") if wrapper is not None else None
    if table is None:
        raise CbrError("таблица кривой доходности не найдена в ответе ЦБ")

    series: dict[str, dict[dt.date, Decimal | None]] = {
        f"{ZCYC_SERIES_PREFIX}{term}": {} for term in ZCYC_TERMS
    }

    matched = False
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        # Строка данных — ровно дата плюс двенадцать сроков. Заголовки и
        # служебные строки отсеиваются этим же условием.
        if len(cells) != 1 + len(ZCYC_TERMS):
            continue
        day = _parse_cbr_date(cells[0].get_text(strip=True))
        if day is None:
            continue
        matched = True
        for position, term in enumerate(ZCYC_TERMS, start=1):
            key = f"{ZCYC_SERIES_PREFIX}{term}"
            series[key][day] = to_decimal(_clean_number(cells[position].get_text(strip=True)))

    if not matched:
        raise CbrError(
            f"в таблице ЦБ нет строк с {len(ZCYC_TERMS)} сроками: разметка страницы изменилась"
        )

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
