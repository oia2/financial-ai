"""Обмен с источником позиций по фьючерсам.

Позиций нет в биржевом интерфейсе данных без подписки, поэтому они берутся с
сайта биржи — **таблицей всех контрактов за дату одним запросом**
(`open-positions-csv.aspx?d=ГГГГММДД&t=1`, spec 008, FR-053a).

Прежде источник повторял оригинал (`pipelines/moex_futures_positions/`) и
отправлял ASP.NET-форму по каждому контракту: около 70 обращений по 3 секунды на
сессию, ~4 минуты на дату и ~5 часов на окно позиций. Таблица за дату несёт те
же числа — сверено на 22.09.2026 по SBRF, GAZR и LENT до единицы — и отвечает
примерно за секунду. Форма вместе с её состоянием (`__VIEWSTATE`) и разбором
HTML удалена: второй путь к тем же данным однажды разошёлся бы с первым.

Правила разбора:

- берутся строки фьючерсов (`contract_type = F`); опционы в наблюдение не входят;
- `iz_fiz = 1` — физические лица, пусто — юридические;
- `moment` обязан совпадать с запрошенной датой: ответ за другой день не
  является наблюдением о запрошенной сессии;
- таблица без строк — день не опубликован: это неизвестность, а не отсутствие.

Семейства, которого в опубликованной таблице нет, могло в тот день ещё не
существовать. Это проверяется датой первой серии семейства в ISS
(`series.json?asset_code=…&show_expired=1`): раньше неё пара неприменима с
основанием (FR-032g). Иначе отсутствие остаётся неизвестным ответом.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import httpx

from financial_ai.config import Settings
from financial_ai.market_data.http_metrics import HttpMetrics
from financial_ai.market_data.interrupt import SourceStoppedError
from financial_ai.market_data.sources.equity_d1 import to_decimal

logger = logging.getLogger(__name__)

REQUEST_URL = "https://www.moex.com/ru/derivatives/open-positions-csv.aspx"
SERIES_PATH = "/statistics/engines/futures/markets/forts/series.json"

# Код семейства в связях бумаг — код базового актива ISS плюс этот суффикс:
# `SBRF` → `SBRF_F`. В таблице позиций семейство названо без суффикса.
CONTRACT_SUFFIX = "_F"

# Броузерный заголовок оставлен от оригинала: без него сайт биржи отвечает
# страницей-заглушкой. Это не обход защиты, а условие обмена.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF_FACTOR = 1.7

# Сколько таблиц дат держать в памяти прогона. Сбор идёт по датам подряд, и
# каждая дата нужна только пока собирается её сессия.
DAY_CACHE_LIMIT = 4

REQUIRED_COLUMNS = (
    "moment",
    "isin",
    "contract_type",
    "iz_fiz",
    "long_position",
    "short_position",
)


class PositionsSourceError(RuntimeError):
    """Обмен с источником позиций не удался или ответ нарушил контракт."""


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Открытые позиции по одному семейству контрактов за одну сессию."""

    trade_date: dt.date
    fiz_long: Decimal | None
    fiz_short: Decimal | None
    jur_long: Decimal | None
    jur_short: Decimal | None

    @property
    def has_values(self) -> bool:
        """Есть ли в снимке хоть одно значение (FR-018)."""
        return any(
            value is not None
            for value in (self.fiz_long, self.fiz_short, self.jur_long, self.jur_short)
        )


class PositionFetchKind(StrEnum):
    """Смысл ответа по одной паре «контракт — дата»."""

    VALUE = "value"
    CONFIRMED_ABSENCE = "confirmed_absence"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PositionFetchResult:
    """Различимый результат пары без перегруженного ``None``."""

    kind: PositionFetchKind
    reason_code: str
    snapshot: PositionSnapshot | None = None

    @classmethod
    def value(cls, snapshot: PositionSnapshot) -> PositionFetchResult:
        return cls(PositionFetchKind.VALUE, "exact_date_values", snapshot)

    @classmethod
    def confirmed_absence(cls, reason_code: str) -> PositionFetchResult:
        return cls(PositionFetchKind.CONFIRMED_ABSENCE, reason_code)

    @classmethod
    def not_applicable(cls, reason_code: str) -> PositionFetchResult:
        return cls(PositionFetchKind.NOT_APPLICABLE, reason_code)

    @classmethod
    def unknown(cls, reason_code: str) -> PositionFetchResult:
        return cls(PositionFetchKind.UNKNOWN, reason_code)


@dataclass(frozen=True, slots=True)
class DayTable:
    """Таблица позиций за дату: снимок по каждому семейству.

    ``published = False`` — биржа отдала только заголовок: день не опубликован.
    """

    day: dt.date
    published: bool
    families: dict[str, PositionSnapshot]


class PositionsClient:
    """Клиент источника позиций: одна таблица на дату, кеш на время прогона."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        should_stop: Callable[[], bool] | None = None,
        request_permit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        # Признак остановки нужен САМОМУ клиенту: повторы с нарастающей паузой
        # тянутся минутами, и доводиться до конца должен отправленный запрос, а
        # не вся серия (FR-058j).
        self.should_stop = should_stop
        self.request_permit = request_permit
        self._client = client
        self._owns_client = client is None
        self.metrics = HttpMetrics()
        self._days: dict[dt.date, DayTable] = {}
        self._family_starts: dict[str, dt.date | None] = {}
        self._first_available: dict[str, dt.date | None] = {}

    async def __aenter__(self) -> PositionsClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.market_data_http_timeout_seconds,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def requests_made(self) -> int:
        """Сколько обращений выполнено (показывает `verify-positions`)."""
        return self.metrics.attempts

    # --- обмен --------------------------------------------------------------

    async def known_contracts(self, day: dt.date) -> set[str]:
        """Коды семейств, у которых за дату есть строки фьючерсов."""
        table = await self._day_table(day)
        return {f"{family}{CONTRACT_SUFFIX}" for family in table.families}

    async def fetch(self, contract_code: str, day: dt.date) -> PositionFetchResult:
        """Позиции по одному семейству за одну сессию — из таблицы даты.

        Таблица даты запрашивается один раз на прогон; остальные семейства той
        же даты берутся из неё без обращений.
        """
        code = contract_code.strip().upper()
        family = code.removesuffix(CONTRACT_SUFFIX)
        table = await self._day_table(day)

        if not table.published:
            return PositionFetchResult.unknown("day_not_published")

        snapshot = table.families.get(family)
        if snapshot is None:
            started = await self.family_started_on(code)
            if started is not None and day < started:
                return PositionFetchResult.not_applicable("contract_family_not_traded_yet")
            return PositionFetchResult.unknown("family_missing_in_day_table")
        if not snapshot.has_values:
            return PositionFetchResult.confirmed_absence("exact_date_empty_position_row")
        return PositionFetchResult.value(snapshot)

    async def family_started_on(self, contract_code: str) -> dt.date | None:
        """Дата первых торгов семейства: самая ранняя серия, включая истёкшие.

        ``None`` — у биржи серий этого семейства нет, и основания считать пару
        неприменимой нет. Спрашивается один раз на семейство за прогон.
        """
        family = contract_code.strip().upper().removesuffix(CONTRACT_SUFFIX)
        if family in self._family_starts:
            return self._family_starts[family]

        body = await self._get(
            f"{self._settings.market_data_iss_base_url.rstrip('/')}{SERIES_PATH}",
            {"asset_code": family, "show_expired": "1", "iss.meta": "off"},
        )
        started = parse_family_start(body, family)
        self._family_starts[family] = started
        return started

    def searched_for_first_date(self, contract_code: str) -> bool:
        """Выполнялся ли уже поиск первой доступной даты по этому контракту."""
        return contract_code.strip().upper() in self._first_available

    async def first_available_date(
        self, contract_code: str, sessions: list[dt.date]
    ) -> dt.date | None:
        """Самая ранняя сессия окна, за которую по контракту НАЙДЕНЫ данные.

        Нижняя граница существования, а не дата появления контракта: про более
        раннее время найденная дата не говорит ничего. Ошибка обращения
        передаётся наружу и отрицательного ответа не оставляет (FR-032).
        """
        code = contract_code.strip().upper()
        if code in self._first_available:
            return self._first_available[code]
        if not sessions:
            return None

        found: dt.date | None = None
        for day in sessions:
            result = await self.fetch(code, day)
            if result.kind is PositionFetchKind.VALUE:
                found = day
                break
        self._first_available[code] = found
        return found

    # --- внутреннее ---------------------------------------------------------

    async def _day_table(self, day: dt.date) -> DayTable:
        """Таблица позиций за дату — один запрос на дату за прогон."""
        cached = self._days.get(day)
        if cached is not None:
            return cached

        body = await self._get(REQUEST_URL, {"d": day.strftime("%Y%m%d"), "t": "1"})
        table = parse_day_table(body, day)
        if len(self._days) >= DAY_CACHE_LIMIT:
            self._days.pop(next(iter(self._days)))
        self._days[day] = table
        logger.info(
            "позиции за %s: %s, семейств фьючерсов %d",
            day,
            "таблица получена" if table.published else "день не опубликован",
            len(table.families),
        )
        return table

    async def _get(self, url: str, params: dict[str, str]) -> str:
        """Одно обращение с повторами; остановка отменяет повторы."""
        if self._client is None:
            raise PositionsSourceError("клиент не инициализирован: используйте async with")

        delay = self._settings.market_data_positions_retry_backoff_seconds
        last_error = "неизвестная причина"

        for attempt in range(1, self._settings.market_data_positions_retries + 1):
            if attempt > 1 and self.should_stop is not None and self.should_stop():
                # Остановка — команда человека, а не неисправность биржи
                # (FR-050, FR-058j).
                raise SourceStoppedError(detail=f"повтор отменён остановкой ({last_error})")
            started = time.monotonic()
            try:
                await self._throttle()
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
                last_error = f"сетевая ошибка: {error or type(error).__name__}"
            else:
                self.metrics.record_attempt(
                    retry=attempt > 1,
                    search_probe=False,
                    elapsed_seconds=time.monotonic() - started,
                )
                if response.status_code == httpx.codes.OK:
                    self.metrics.record_success()
                    return response.content.decode("utf-8-sig", errors="strict")
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise PositionsSourceError(
                        f"источник позиций ответил {response.status_code}: повтор не поможет"
                    )
                last_error = f"HTTP {response.status_code}"

            if attempt < self._settings.market_data_positions_retries:
                logger.warning(
                    "позиции: попытка %d не удалась (%s), повтор через %.1f с",
                    attempt,
                    last_error,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= RETRY_BACKOFF_FACTOR

        raise PositionsSourceError(f"источник позиций недоступен ({last_error})")

    async def _throttle(self) -> None:
        """Пауза между пачками обращений; темп задан конфигурацией."""
        size = self._settings.market_data_positions_batch_size
        pause = self._settings.market_data_positions_batch_pause_seconds
        if self.should_stop is not None and self.should_stop():
            return
        if self.metrics.attempts and pause and self.metrics.attempts % size == 0:
            await asyncio.sleep(pause)


# --- разбор -------------------------------------------------------------------


def parse_day_table(body: str, day: dt.date) -> DayTable:
    """Таблица позиций за дату из ответа биржи.

    Нарушение контракта — отсутствующая колонка, чужая дата, повтор строки
    семейства и стороны, нераспознанное число — не превращается в пустоту:
    это ошибка источника (FR-032e).
    """
    # Ответ начинается с BOM; снимается и здесь, а не только при декодировании.
    reader = csv.DictReader(io.StringIO(body.removeprefix("﻿")))
    columns = reader.fieldnames or []
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        raise PositionsSourceError(
            f"таблица позиций без обязательных колонок: {', '.join(missing)}"
        )

    sides: dict[str, dict[str, tuple[Decimal | None, Decimal | None]]] = {}
    published = False
    for position, row in enumerate(reader):
        moment = (row.get("moment") or "").strip()
        if not moment:
            continue
        published = True
        if moment[:10] != day.isoformat():
            raise PositionsSourceError(
                f"таблица позиций за {day}: строка {position} относится к {moment[:10]}"
            )
        if (row.get("contract_type") or "").strip() != "F":
            continue
        family = (row.get("isin") or "").strip().upper()
        if not family:
            raise PositionsSourceError(f"таблица позиций за {day}: строка {position} без кода")
        side = "fiz" if _is_fiz(row.get("iz_fiz")) else "jur"
        by_side = sides.setdefault(family, {})
        if side in by_side:
            raise PositionsSourceError(f"таблица позиций за {day}: повтор строки {family} ({side})")
        by_side[side] = (_number(row.get("long_position")), _number(row.get("short_position")))

    families = {
        family: PositionSnapshot(
            trade_date=day,
            fiz_long=by_side.get("fiz", (None, None))[0],
            fiz_short=by_side.get("fiz", (None, None))[1],
            jur_long=by_side.get("jur", (None, None))[0],
            jur_short=by_side.get("jur", (None, None))[1],
        )
        for family, by_side in sides.items()
    }
    return DayTable(day=day, published=published, families=families)


def parse_family_start(body: str, family: str) -> dt.date | None:
    """Самая ранняя дата начала серии семейства из ответа ISS."""
    payload = json.loads(body)
    block = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        raise PositionsSourceError("ответ ISS о сериях не содержит блок series")
    columns = block.get("columns")
    data = block.get("data")
    if not isinstance(columns, list) or not isinstance(data, list):
        raise PositionsSourceError("блок series: нет columns или data")
    try:
        code_at = columns.index("asset_code")
        start_at = columns.index("start_date")
        name_at = columns.index("name")
    except ValueError as error:
        raise PositionsSourceError("блок series: нет asset_code, start_date или name") from error

    starts: list[dt.date] = []
    for row in data:
        if not isinstance(row, list) or str(row[code_at]).upper() != family:
            continue
        # Только фьючерс с одним сроком (`FIXR-9.26`). Календарный спред
        # (`FIXR-9.26-12.26`) и служебные серии появляются раньше самих
        # фьючерсов: у FIXR спред начался 17.08.2026, фьючерс — 18.08, и
        # позиций за 17.08 в таблице биржи законно нет.
        if len(_EXPIRY_TOKEN.findall(str(row[name_at]))) != 1:
            continue
        try:
            starts.append(dt.date.fromisoformat(str(row[start_at])[:10]))
        except ValueError as error:
            raise PositionsSourceError(
                f"блок series: некорректная дата {row[start_at]!r}"
            ) from error
    return min(starts) if starts else None


_EXPIRY_TOKEN = re.compile(r"\d{1,2}\.\d{2}(?!\d)")


def _is_fiz(raw: str | None) -> bool:
    value = (raw or "").strip()
    if not value:
        return False
    parsed = to_decimal(value)
    if parsed not in (Decimal("0"), Decimal("1")):
        raise PositionsSourceError(f"признак физического лица {raw!r} не 0 и не 1")
    return parsed == Decimal("1")


def _number(raw: str | None) -> Decimal | None:
    """Число таблицы: пусто — законный пропуск, прочее обязано разобраться."""
    return to_decimal((raw or "").strip())
