"""Клиент MOEX ISS.

Перенесён из `pipelines/iss_shared/iss_client.py` (`MR-MASTER-DRO`, `f07295e`)
с заменой `requests` на `httpx`: он уже есть в зависимостях проекта, и вторая
HTTP-библиотека здесь не нужна. Поведение повторов сохранено.

Данные MOEX ISS публичны — ни токена, ни иных секретов клиент не принимает.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

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
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        # Признак остановки нужен САМОМУ клиенту: шесть попыток с нарастающей
        # паузой ждут минутами, и остановка, пришедшая в неудачный момент,
        # ждала их все. Доводится до конца отправленный запрос — не серия из
        # повторов (FR-058j).
        self.should_stop = should_stop

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
        )

    async def fetch_session_rows_for(
        self,
        session_date: str,
        columns: tuple[str, ...],
        engine: str | None = None,
        market: str | None = None,
        board: str | None = None,
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
            lambda start: urls.history_by_date_params(
                session_date, start, self._config.page_limit, columns
            ),
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
        )

    async def fetch_futures_series(self) -> list[dict[str, Any]]:
        """Серии срочного рынка: базовый актив и код контракта.

        Нужны позициям: код контракта из тикера акции не выводится, а список
        серий связывает их напрямую.
        """
        payload = await self._get_json(
            urls.futures_series_url(self._config.base_url), {"iss.meta": "off"}
        )
        block = payload.get("series") or {}
        return _rows_to_dicts(block.get("columns") or [], block.get("data") or [])

    async def fetch_futures_open_interest(self) -> dict[str, int]:
        """Открытый интерес по кодам базовых активов срочного рынка.

        Разрешает выбор, когда у одной акции несколько кодов контракта:
        классический и вечный, обычный и мини. Позиции живут там, где торгуют.
        """
        payload = await self._get_json(
            urls.futures_securities_url(self._config.base_url),
            {"iss.meta": "off", "iss.only": "securities"},
        )
        block = payload.get("securities") or {}
        rows = _rows_to_dicts(block.get("columns") or [], block.get("data") or [])

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
        block = payload.get("securities") or {}
        rows = _rows_to_dicts(block.get("columns") or [], block.get("data") or [])

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
        block = payload.get("securities") or {}
        rows = _rows_to_dicts(block.get("columns") or [], block.get("data") or [])

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
        block = payload.get("description") or {}
        rows = _rows_to_dicts(block.get("columns") or [], block.get("data") or [])

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
            block = payload.get("analytics") or {}
            data = block.get("data") or []
            if not data:
                break

            rows.extend(_rows_to_dicts(block.get("columns") or [], data))
            if len(data) < self._config.page_limit:
                break
            start += len(data)

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
        block = payload.get("indices") or {}
        rows = _rows_to_dicts(block.get("columns") or [], block.get("data") or [])

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

    async def _paginate(self, url: str, params_for: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = await self._get_json(url, params_for(start))
            block = payload.get("history") or {}
            data = block.get("data") or []
            if not data:
                break
            rows.extend(_rows_to_dicts(block.get("columns") or [], data))
            if len(data) < self._config.page_limit:
                break
            start += self._config.page_limit
        return rows

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise IssError("клиент не инициализирован: используйте async with")

        delay = self._config.initial_retry_delay_seconds
        last_error: str = "неизвестная причина"

        for attempt in range(1, self._config.retries + 1):
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as error:
                last_error = f"сетевая ошибка: {error}"
            else:
                if response.status_code == httpx.codes.OK:
                    try:
                        payload: dict[str, Any] = response.json()
                    except ValueError as error:
                        raise IssError(f"ответ MOEX ISS не является JSON: {error}") from error
                    return payload
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise IssError(
                        f"MOEX ISS ответил {response.status_code} на {url}: повтор не поможет"
                    )
                last_error = f"HTTP {response.status_code}"

            if self.should_stop is not None and self.should_stop():
                raise IssError(f"повтор отменён остановкой ({last_error}): {url}")

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


def _rows_to_dicts(columns: list[str], data: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row, strict=False)) for row in data]
