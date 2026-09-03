"""Клиент MOEX ISS.

Перенесён из `pipelines/iss_shared/iss_client.py` (`MR-MASTER-DRO`, `f07295e`)
с заменой `requests` на `httpx`: он уже есть в зависимостях проекта, и вторая
HTTP-библиотека здесь не нужна. Поведение повторов сохранено.

Данные MOEX ISS публичны — ни токена, ни иных секретов клиент не принимает.
"""

from __future__ import annotations

import asyncio
import logging
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

    def __init__(self, config: IssConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

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
            board if board is not None else self._config.board,
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
            board if board is not None else self._config.board,
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
