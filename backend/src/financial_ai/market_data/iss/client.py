"""Клиент MOEX ISS.

Перенесён из `pipelines/iss_shared/iss_client.py` (`MR-MASTER-DRO`, `f07295e`)
с заменой `requests` на `httpx`: он уже есть в зависимостях проекта, и вторая
HTTP-библиотека здесь не нужна. Поведение повторов сохранено.

Данные MOEX ISS публичны — ни токена, ни иных секретов клиент не принимает.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from financial_ai.market_data.http_metrics import HttpMetrics
from financial_ai.market_data.interrupt import SourceStoppedError
from financial_ai.market_data.iss import urls

logger = logging.getLogger(__name__)

# Коды, при которых повтор осмыслен. 429 в этом списке не случайно: биржа
# ограничивает частоту, и с ограничением уже сталкивались.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

INITIAL_RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 15.0
RETRY_BACKOFF_FACTOR = 1.7


class IssError(RuntimeError):
    """Обращение к MOEX ISS не удалось."""


@dataclass(frozen=True, slots=True)
class IssConfig:
    """Параметры обращения к бирже."""

    base_url: str = "https://iss.moex.com/iss"
    board: str = "TQBR"
    engine: str = "stock"
    market: str = "shares"
    page_limit: int = 100
    retries: int = 6
    timeout_seconds: float = 60.0
    # Настраивается ради тестов: с боевой паузой прогон повторов занимал бы
    # секунды, а медленный набор тестов перестают запускать.
    initial_retry_delay_seconds: float = INITIAL_RETRY_DELAY_SECONDS


class IssClient:
    """Чтение исторических данных с MOEX ISS."""

    def __init__(
        self,
        config: IssConfig,
        client: httpx.AsyncClient | None = None,
        should_stop: Callable[[], bool] | None = None,
        request_permit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        # Признак остановки нужен САМОМУ клиенту: шесть попыток с нарастающей
        # паузой ждут минутами, и остановка, пришедшая в неудачный момент,
        # ждала их все. Доводится до конца отправленный запрос — не серия из
        # повторов (FR-058j).
        self.should_stop = should_stop
        self.request_permit = request_permit
        self.metrics = HttpMetrics()

    async def __aenter__(self) -> IssClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_session_rows(
        self, session_date: str, columns: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Данные одной торговой сессии по всем бумагам доски.

        Форма для ежедневного добора: одно обращение вместо одного на бумагу.
        """
        url = urls.history_by_date_url(
            self._config.base_url, self._config.board, self._config.engine, self._config.market
        )
        return await self._paginate(
            url,
            lambda start: urls.history_by_date_params(
                session_date, start, self._config.page_limit, columns
            ),
            required_columns=columns,
            date_from=session_date,
            date_till=session_date,
        )

    async def fetch_session_rows_for(
        self,
        session_date: str,
        columns: tuple[str, ...],
        engine: str | None = None,
        market: str | None = None,
        board: str | None = None,
        assetcode: str | None = None,
    ) -> list[dict[str, Any]]:
        """Данные сессии в указанном разделе торгов.

        Нужен для фьючерсов: они живут в `futures/forts`, а не в `stock/shares`.
        Клиент при этом остаётся одним — см. `fetch_security_history`.
        """
        url = urls.history_by_date_url(
            self._config.base_url,
            self._board_for(board, engine, market),
            engine or self._config.engine,
            market or self._config.market,
        )
        return await self._paginate(
            url,
            lambda start: (
                urls.history_by_date_params(session_date, start, self._config.page_limit, columns)
                | ({"assetcode": assetcode} if assetcode is not None else {})
            ),
            required_columns=columns,
            date_from=session_date,
            date_till=session_date,
        )

    async def fetch_security_history(
        self,
        secid: str,
        date_from: str,
        date_till: str,
        columns: tuple[str, ...],
        engine: str | None = None,
        market: str | None = None,
        board: str | None = None,
    ) -> list[dict[str, Any]]:
        """История одной бумаги за диапазон дат.

        Форма для первичной загрузки: там перебор по бумагам на месте.

        Раздел торгов переопределяется на вызов: индексы живут в `stock/index`,
        валюта — в `currency/selt`. Это дешевле, чем заводить отдельный клиент
        под каждый ряд, и — главное — оставляет клиент **одним**: подделка в
        тестах перехватывает все источники сразу, и ни один не уходит в сеть.
        """
        url = urls.history_by_security_url(
            self._config.base_url,
            self._board_for(board, engine, market),
            secid,
            engine or self._config.engine,
            market or self._config.market,
        )
        return await self._paginate(
            url,
            lambda start: urls.history_by_security_params(
                date_from, date_till, start, self._config.page_limit, columns
            ),
            required_columns=columns,
            date_from=date_from,
            date_till=date_till,
        )

    async def fetch_futures_series(self) -> list[dict[str, Any]]:
        """Серии срочного рынка: базовый актив и код контракта.

        Нужны позициям: код контракта из тикера акции не выводится, а список
        серий связывает их напрямую.
        """
        payload = await self._get_json(
            urls.futures_series_url(self._config.base_url), {"iss.meta": "off"}
        )
        columns, data = _validated_block(
            payload,
            "series",
            required_columns=("underlying_asset", "asset_code", "secid"),
        )
        return _rows_to_dicts(columns, data)

    async def fetch_futures_open_interest(self) -> dict[str, int]:
        """Открытый интерес по кодам базовых активов срочного рынка.

        Разрешает выбор, когда у одной акции несколько кодов контракта:
        классический и вечный, обычный и мини. Позиции живут там, где торгуют.
        """
        payload = await self._get_json(
            urls.futures_securities_url(self._config.base_url),
            {"iss.meta": "off", "iss.only": "securities"},
        )
        columns, data = _validated_block(
            payload,
            "securities",
            required_columns=("ASSETCODE", "PREVOPENPOSITION"),
        )
        rows = _rows_to_dicts(columns, data)

        totals: dict[str, int] = {}
        for row in rows:
            code = row.get("ASSETCODE")
            if not isinstance(code, str) or not code.strip():
                continue
            value = row.get("PREVOPENPOSITION")
            totals[code.strip().upper()] = totals.get(code.strip().upper(), 0) + int(value or 0)
        return totals

    async def fetch_equity_lot_sizes(self) -> dict[str, int]:
        """Размеры лотов бумаг доски.

        Лот нужен плану портфеля: на бирже торгуют лотами, и план в дробных
        акциях неисполним. Входом модели он не является, поэтому его отсутствие
        не делает дату неготовой.
        """
        payload = await self._get_json(
            urls.equity_securities_url(self._config.base_url, self._config.board),
            {"iss.meta": "off", "iss.only": "securities"},
        )
        columns, data = _validated_block(
            payload, "securities", required_columns=("SECID", "LOTSIZE")
        )
        rows = _rows_to_dicts(columns, data)

        lots: dict[str, int] = {}
        for row in rows:
            ticker = row.get("SECID")
            value = row.get("LOTSIZE")
            if not isinstance(ticker, str) or not ticker.strip():
                continue
            if value is None:
                continue
            try:
                lot = int(value)
            except (TypeError, ValueError):
                # Отсутствующий лот пропускается, а не подменяется единицей:
                # выдуманный лот дал бы неисполнимый план, а прочерк честен.
                continue
            if lot > 0:
                lots[ticker.strip().upper()] = lot

        return lots

    async def fetch_equity_isins(self) -> dict[str, str]:
        """Устойчивые идентификаторы бумаг доски.

        Тикер — имя бумаги на период, а не сама бумага: при переименовании он
        меняется, и связь с фьючерсом рвётся молча. ISIN не меняется, поэтому
        переименование по нему опознаётся как переименование, а не как новая
        бумага (spec 008, FR-018). Сверено на живом источнике 2026-09-17:
        доска отдаёт ISIN, у фьючерсов его нет.
        """
        payload = await self._get_json(
            urls.equity_securities_url(self._config.base_url, self._config.board),
            {"iss.meta": "off", "iss.only": "securities"},
        )
        columns, data = _validated_block(payload, "securities", required_columns=("SECID", "ISIN"))
        rows = _rows_to_dicts(columns, data)

        isins: dict[str, str] = {}
        for row in rows:
            ticker = row.get("SECID")
            isin = row.get("ISIN")
            if not isinstance(ticker, str) or not isinstance(isin, str):
                continue
            if ticker.strip() and isin.strip():
                isins[ticker.strip().upper()] = isin.strip().upper()
        return isins

    async def fetch_emitter_id(self, secid: str) -> str | None:
        """Идентификатор эмитента инструмента.

        Единственное поле, которым связь акции и контракта подтверждается
        независимо от совпадения названий: идентификатора базовой бумаги в
        описании фьючерса нет вовсе (сверено 2026-09-17, см. PROVENANCE.md).
        Эмитента мало, чтобы ВЫБРАТЬ бумагу — `SBER` и `SBERP` неразличимы по
        нему, — поэтому он служит проверкой, а не основанием выбора.

        ``None`` означает «источник поля не дал», а не «эмитенты разные».
        """
        payload = await self._get_json(
            urls.security_description_url(self._config.base_url, secid),
            {"iss.meta": "off", "iss.only": "description"},
        )
        columns, data = _validated_block(payload, "description", required_columns=("name", "value"))
        rows = _rows_to_dicts(columns, data)

        for row in rows:
            if row.get("name") == "EMITTER_ID":
                value = row.get("value")
                if value is None or not str(value).strip():
                    return None
                return str(value).strip()
        return None

    async def fetch_index_analytics(
        self, index_id: str, session_date: str | None = None
    ) -> list[dict[str, Any]]:
        """Состав индекса с весами бумаг.

        ``session_date`` пустой означает «текущий состав» — так по умолчанию
        работает оригинал, и так же устроен наш справочник секторов: у него нет
        оси сессий.

        Страницы листаются курсором раздела: состав широкого индекса не
        помещается в одну страницу, а недобранный хвост выглядел бы как
        выбывшие из индекса бумаги.
        """
        url = urls.index_analytics_url(self._config.base_url, index_id)
        rows: list[dict[str, Any]] = []
        start = 0
        seen_pages: set[str] = set()

        while True:
            params: dict[str, Any] = {
                "iss.meta": "off",
                "iss.only": "analytics,analytics.cursor",
                "start": start,
                "limit": self._config.page_limit,
            }
            if session_date:
                params["date"] = session_date

            payload = await self._get_json(url, params)
            columns, data = _validated_block(
                payload,
                "analytics",
                required_columns=("ticker", "weight", "tradedate"),
            )
            page = _rows_to_dicts(columns, data)
            if session_date is not None:
                _validate_trade_dates(page, session_date, session_date, "analytics")
            rows.extend(page)
            start, complete = _page_progress(
                payload,
                "analytics",
                requested_start=start,
                data=data,
                seen_pages=seen_pages,
            )
            if complete:
                break

        return rows

    async def fetch_index_titles(self) -> dict[str, str]:
        """Краткие имена индексов: `MOEXOG` — «Индекс нефти и газа».

        Секторы называются именами отраслевых индексов, а не собственными
        строками: собственная строка была бы нашей выдумкой поверх биржи.
        """
        payload = await self._get_json(
            urls.index_titles_url(self._config.base_url),
            {"iss.meta": "off", "iss.only": "indices"},
        )
        columns, data = _validated_block(
            payload, "indices", required_columns=("indexid", "shortname")
        )
        rows = _rows_to_dicts(columns, data)

        titles: dict[str, str] = {}
        for row in rows:
            index_id = row.get("indexid")
            title = row.get("shortname")
            if isinstance(index_id, str) and index_id.strip():
                titles[index_id.strip().upper()] = (
                    str(title).strip() if isinstance(title, str) and title.strip() else ""
                )
        return titles

    def _board_for(self, board: str | None, engine: str | None, market: str | None) -> str | None:
        """Какую доску ставить в адрес.

        **Доска осмысленна только в своём разделе.** `TQBR` — доска акций, и в
        адресе индексов или срочного рынка её быть не может: биржа отвечает
        пустым списком, а не ошибкой, поэтому дефект выглядит как отсутствие
        данных. Так пропали и Brent, и четыре индекса — по 1 сессии из 314.

        Отсюда правило: явно заданная доска берётся как есть; доска из
        конфигурации подставляется **только** в тот раздел, к которому она
        относится; в любом другом разделе сегмента доски нет.
        """
        if board is not None:
            return board
        same_section = (engine or self._config.engine) == self._config.engine and (
            market or self._config.market
        ) == self._config.market
        return self._config.board if same_section else None

    async def _paginate(
        self,
        url: str,
        params_for: Any,
        *,
        required_columns: tuple[str, ...],
        date_from: str,
        date_till: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        seen_pages: set[str] = set()
        while True:
            payload = await self._get_json(url, params_for(start))
            columns, data = _validated_block(payload, "history", required_columns=required_columns)
            page = _rows_to_dicts(columns, data)
            _validate_trade_dates(page, date_from, date_till, "history")
            rows.extend(page)
            start, complete = _page_progress(
                payload,
                "history",
                requested_start=start,
                data=data,
                seen_pages=seen_pages,
            )
            if complete:
                break
        return rows

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise IssError("клиент не инициализирован: используйте async with")

        delay = self._config.initial_retry_delay_seconds
        last_error: str = "неизвестная причина"

        for attempt in range(1, self._config.retries + 1):
            if self.should_stop is not None and self.should_stop():
                raise SourceStoppedError()
            try:
                if self.request_permit is not None:
                    await self.request_permit()
                started = time.monotonic()
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as error:
                self.metrics.record_attempt(
                    retry=attempt > 1,
                    search_probe=False,
                    elapsed_seconds=time.monotonic() - started,
                )
                last_error = f"сетевая ошибка: {error}"
            else:
                self.metrics.record_attempt(
                    retry=attempt > 1,
                    search_probe=False,
                    elapsed_seconds=time.monotonic() - started,
                )
                if response.status_code == httpx.codes.OK:
                    self.metrics.record_success()
                    try:
                        payload = _loads(response.content)
                    except ValueError as error:
                        raise IssError(f"ответ MOEX ISS не является JSON: {error}") from error
                    if not isinstance(payload, dict):
                        raise IssError("ответ MOEX ISS: корень JSON должен быть объектом")
                    return payload
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise IssError(
                        f"MOEX ISS ответил {response.status_code} на {url}: повтор не поможет"
                    )
                last_error = f"HTTP {response.status_code}"

            if self.should_stop is not None and self.should_stop():
                raise SourceStoppedError(detail=f"повтор отменён остановкой ({last_error})")

            if attempt < self._config.retries:
                logger.warning(
                    "MOEX ISS: попытка %d из %d не удалась (%s), повтор через %.1f с",
                    attempt,
                    self._config.retries,
                    last_error,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * RETRY_BACKOFF_FACTOR, MAX_RETRY_DELAY_SECONDS)

        raise IssError(
            f"MOEX ISS недоступен после {self._config.retries} попыток ({last_error}): {url}"
        )


def _loads(body: bytes) -> object:
    """Разобрать ответ биржи, переводя дробные числа сразу в ``Decimal``.

    ``response.json()`` разбирает их в ``float``, и на этом значение биржи уже
    искажено: `123456789.123456789` становится `123456789.12345679`. Перевод в
    ``Decimal`` после этого потерю не возвращает — восстанавливать нечего.
    Поэтому точка перевода одна и стоит она в разборе, а не у потребителя.

    Целые числа `json` разбирает в ``int`` и без нас: там теряться нечему.
    """
    parsed: object = json.loads(body, parse_float=Decimal)
    return parsed


def _rows_to_dicts(columns: list[str], data: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row, strict=True)) for row in data]


def _validated_block(
    payload: dict[str, Any], block_name: str, *, required_columns: tuple[str, ...]
) -> tuple[list[str], list[list[Any]]]:
    """Проверить форму блока до того, как пустота получит бизнес-смысл."""
    if block_name not in payload:
        raise IssError(f"ответ MOEX ISS не содержит блок {block_name!r}")
    block = payload[block_name]
    if not isinstance(block, dict):
        raise IssError(f"блок {block_name!r} должен быть объектом")

    columns = block.get("columns")
    data = block.get("data")
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise IssError(f"блок {block_name!r}: columns должен быть списком строк")
    if len(columns) != len(set(columns)):
        raise IssError(f"блок {block_name!r}: имена колонок не должны повторяться")
    missing = [column for column in required_columns if column not in columns]
    if missing:
        raise IssError(
            f"блок {block_name!r}: отсутствуют обязательные колонки {', '.join(missing)}"
        )
    if not isinstance(data, list):
        raise IssError(f"блок {block_name!r}: data должен быть списком строк")
    for position, row in enumerate(data):
        if not isinstance(row, list):
            raise IssError(f"блок {block_name!r}: строка {position} должна быть списком")
        if len(row) != len(columns):
            raise IssError(
                f"блок {block_name!r}: строка {position} содержит {len(row)} значений "
                f"для {len(columns)} колонок"
            )
    return columns, data


def _validate_trade_dates(
    rows: list[dict[str, Any]], date_from: str, date_till: str, block_name: str
) -> None:
    """Не позволить записать ответ за чужой день под запрошенной датой."""
    if not rows or ("TRADEDATE" not in rows[0] and "tradedate" not in rows[0]):
        return
    lower = dt.date.fromisoformat(date_from)
    upper = dt.date.fromisoformat(date_till)
    key = "TRADEDATE" if "TRADEDATE" in rows[0] else "tradedate"
    for position, row in enumerate(rows):
        raw = row.get(key)
        try:
            day = dt.date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError) as error:
            raise IssError(
                f"блок {block_name!r}: строка {position} содержит некорректную дату {raw!r}"
            ) from error
        if not lower <= day <= upper:
            raise IssError(
                f"блок {block_name!r}: строка {position} относится к чужой дате {day}; "
                f"запрошено {lower}..{upper}"
            )


def _page_progress(
    payload: dict[str, Any],
    block_name: str,
    *,
    requested_start: int,
    data: list[list[Any]],
    seen_pages: set[str],
) -> tuple[int, bool]:
    """Определить следующий start по курсору или фактической странице.

    Сервер вправе ограничить страницу сильнее запрошенного ``limit``. Поэтому
    короткая непустая страница не означает конец, а шаг всегда равен числу
    реально полученных строк.
    """
    if data:
        fingerprint = repr(data)
        if fingerprint in seen_pages:
            raise IssError(f"блок {block_name!r}: сервер повторил страницу без продвижения")
        seen_pages.add(fingerprint)

    cursor_name = f"{block_name}.cursor"
    if cursor_name not in payload:
        if not data:
            return requested_start, True
        next_start = requested_start + len(data)
        if next_start <= requested_start:
            raise IssError(f"блок {block_name!r}: пагинация не продвинулась")
        return next_start, False

    cursor_columns, cursor_data = _validated_block(
        payload,
        cursor_name,
        required_columns=("INDEX", "TOTAL", "PAGESIZE"),
    )
    if len(cursor_data) != 1:
        raise IssError(f"блок {cursor_name!r}: ожидается ровно одна строка курсора")
    cursor = _rows_to_dicts(cursor_columns, cursor_data)[0]
    index = _cursor_integer(cursor.get("INDEX"), cursor_name, "INDEX", minimum=0)
    total = _cursor_integer(cursor.get("TOTAL"), cursor_name, "TOTAL", minimum=0)
    page_size = _cursor_integer(cursor.get("PAGESIZE"), cursor_name, "PAGESIZE", minimum=1)
    if index != requested_start:
        raise IssError(
            f"блок {cursor_name!r}: INDEX={index}, хотя запрошен start={requested_start}"
        )
    if len(data) > page_size:
        raise IssError(f"блок {cursor_name!r}: пришло {len(data)} строк при PAGESIZE={page_size}")
    next_start = index + len(data)
    if next_start > total:
        raise IssError(
            f"блок {cursor_name!r}: страница заканчивается на {next_start}, TOTAL={total}"
        )
    if not data and next_start < total:
        raise IssError(
            f"блок {cursor_name!r}: пустая страница до конца диапазона ({next_start} из {total})"
        )
    return next_start, next_start >= total


def _cursor_integer(raw: object, block_name: str, column: str, *, minimum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise IssError(
            f"блок {block_name!r}: {column} должен быть целым числом не меньше {minimum}"
        )
    return raw
