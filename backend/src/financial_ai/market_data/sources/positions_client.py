"""Обмен с источником позиций по фьючерсам.

**Это вторая интеграция с биржей, и она устроена иначе, чем все остальные.**
Позиций нет в биржевом интерфейсе данных ни в каком разделе: оригинал
(`pipelines/moex_futures_positions/`) берёт их формой с сайта биржи, получает
HTML и делает это по обращению **на инструмент и дату**. Отсюда и правила
аккуратности: этот источник — самый нагруженный в системе.

Три расхождения с оригиналом, каждое установлено на живом ответе.

**Состояние страницы берётся у страницы, а не из файла.** Оригинал носит с
собой захваченный когда-то `__VIEWSTATE` на 128 КБ и подставляет в него тикер
текстом. Подстановка выбранный инструмент **не меняет**: запрос «по SBRF_F» с
этим телом возвращает позиции по ALRS — контракту, который был выбран в момент
захвата. Проверено 2026-09-04 на дате 2026-08-28: ответ совпал с ответом по
`ALRS_F` до последней цифры (373 194), тогда как настоящий SBRF даёт 1 027 076
при открытом интересе ISS 1 045 330. Здесь состояние запрашивается у самой
страницы и обновляется из каждого ответа — устареть оно не может.

**Строка таблицы ищется по названию, а не по позиции.** Оригинал отображает
строки по порядковому номеру. Порядок строк в живых ответах различается даже
между двумя запросами подряд, и позиционное отображение записало бы число
держателей позиций в столбец открытых позиций.

**Соответствие акции и контракта строится из ISS.** Правилом код не выводится
(`SBER → SBRF_F`, `NVTK → NOTKM_F`), а файла соответствий оригинала в
репозитории нет. Само правило выбора живёт в `market_data/links.py` и здесь не
повторяется: два кода одного правила однажды разошлись бы, и выяснилось бы это
на данных.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from financial_ai.config import Settings
from financial_ai.market_data.sources.equity_d1 import to_decimal

logger = logging.getLogger(__name__)

REQUEST_URL = "https://www.moex.com/ru/derivatives/open-positions-new.aspx/open-positions-csv.aspx"

# Код инструмента на странице открытых позиций — код базового актива ISS плюс
# этот суффикс: `SBRF` → `SBRF_F`.
CONTRACT_SUFFIX = "_F"

# Броузерный заголовок оставлен от оригинала: без него сайт биржи отвечает
# страницей-заглушкой. Это не обход защиты, а условие обмена.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF_FACTOR = 1.7

# Строка таблицы с открытыми позициями. Соседние строки — число держателей и
# изменения за день; они относятся к инженерии признаков и не переносятся.
OPEN_POSITION_TITLE = "количество договоров"

INSTRUMENT_SELECTOR = "a[id^='ctl00_PageContent_RepeaterInstr_'][id$='_Instrum']"
TABLE_SELECTOR = "table.table1._full-width.table1"

CONTRACT_CODE_RE = re.compile(r"\(([^)]+_F)\)", re.IGNORECASE)
POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)'")

RUSSIAN_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


class PositionsSourceError(RuntimeError):
    """Обмен с источником позиций не удался."""


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Открытые позиции по одному контракту за одну сессию."""

    trade_date: dt.date
    fiz_long: Decimal | None
    fiz_short: Decimal | None
    jur_long: Decimal | None
    jur_short: Decimal | None

    @property
    def has_values(self) -> bool:
        """Есть ли в снимке хоть одно значение.

        Отличает настоящую частичность от пустоты: именно на этом различии
        держится FR-018.
        """
        return any(
            value is not None
            for value in (self.fiz_long, self.fiz_short, self.jur_long, self.jur_short)
        )


@dataclass(frozen=True, slots=True)
class Instrument:
    """Как попросить у страницы конкретный контракт."""

    event_target: str
    selected_text: str


@dataclass
class PageState:
    """Состояние формы: скрытые поля и список инструментов.

    Обновляется из каждого ответа. `__VIEWSTATE` и `__EVENTVALIDATION` меняются
    от запроса к запросу, и хранить их дольше одного обмена нельзя.
    """

    viewstate: str = ""
    generator: str = ""
    validation: str = ""
    instruments: dict[str, Instrument] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return bool(self.viewstate and self.instruments)


class PositionsClient:
    """Клиент источника позиций.

    Темп ограничивается самим клиентом, а не вызывающим: единица обращения
    здесь мельче сессии, и правило «пауза между пачками» иначе пришлось бы
    повторять в каждом месте вызова.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._state = PageState()
        self._requests_made = 0
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
        """Сколько обращений выполнено.

        Показывается живой сверкой `verify-positions`: единица обращения здесь —
        инструмент и дата, и цена сбора в обращениях важнее, чем у остальных
        источников.
        """
        return self._requests_made

    # --- обмен --------------------------------------------------------------

    async def known_contracts(self, day: dt.date) -> set[str]:
        """Коды контрактов, которые страница знает на эту дату."""
        await self._ensure_state(day)
        return set(self._state.instruments)

    async def fetch(self, contract_code: str, day: dt.date) -> PositionSnapshot | None:
        """Позиции по одному контракту за одну сессию.

        ``None`` означает «данных за эту дату нет»: либо таблицы в ответе не
        оказалось, либо биржа ответила снимком за другую дату. Второе —
        не ошибка обмена, а отсутствие торгов, и записывать такой ответ как
        наблюдение о запрошенной сессии значило бы передатировать его.
        """
        code = contract_code.strip().upper()
        await self._ensure_state(day)

        instrument = self._state.instruments.get(code)
        if instrument is None:
            logger.debug("контракта %s нет в списке инструментов на %s", code, day)
            return None

        html = await self._post(self._data_fields(instrument, day), day)
        snapshot = parse_snapshot(html)
        self._absorb_state(html)

        if snapshot is None:
            return None
        if snapshot.trade_date != day:
            # Биржа отдаёт последний доступный снимок, если за дату данных нет.
            logger.debug(
                "позиции %s: запрошено %s, ответ за %s — считаем отсутствием",
                code,
                day,
                snapshot.trade_date,
            )
            return None
        return snapshot

    def searched_for_first_date(self, contract_code: str) -> bool:
        """Выполнялся ли уже поиск первой доступной даты по этому контракту.

        Нужно вызывающему, чтобы отличить «искали и не нашли ничего» от «ещё не
        искали». В первом случае обращения по контракту бесполезны, во втором —
        наоборот, обязательны.
        """
        return contract_code.strip().upper() in self._first_available

    async def first_available_date(
        self, contract_code: str, sessions: list[dt.date]
    ) -> dt.date | None:
        """Первая сессия, за которую по контракту вообще есть данные.

        ``None`` означает «искали по всему переданному окну и не нашли», если
        поиск выполнялся, и «ещё не искали», если сессий не передали. Различить
        помогает :meth:`searched_for_first_date`.

        **Отметка из собранных данных сюда не передаётся, и это не упущение.**
        Самая ранняя собранная сессия доказывает, что инструмент тогда
        существовал, но про более раннее время не говорит ничего. Подстановка её
        как ответа запрещала бы догон истории навсегда — сессии старше уже
        собранных пропускались бы как несуществующие. Вызывающий использует её,
        чтобы вовсе не звать этот поиск для сессий не старше отметки.

        Поиск — как в оригинале: редкая сетка проб, затем уточнение внутри
        последнего шага. Выполняется однократно за время жизни клиента.
        """
        code = contract_code.strip().upper()
        if code in self._first_available:
            return self._first_available[code]
        if not sessions:
            return None

        step = max(1, self._settings.market_data_positions_discover_step)
        probes = list(range(0, len(sessions), step))
        if probes[-1] != len(sessions) - 1:
            probes.append(len(sessions) - 1)

        hit: int | None = None
        for position in probes:
            if await self._has_data(code, sessions[position]):
                hit = position
                break

        if hit is None:
            logger.info("позиции %s: данных нет ни в одной пробе окна", code)
            self._first_available[code] = None
            return None

        found = sessions[hit]
        for position in range(max(0, hit - step), hit):
            if await self._has_data(code, sessions[position]):
                found = sessions[position]
                break

        logger.info("позиции %s: первая доступная дата %s", code, found)
        self._first_available[code] = found
        return found

    async def _has_data(self, contract_code: str, day: dt.date) -> bool:
        try:
            snapshot = await self.fetch(contract_code, day)
        except PositionsSourceError:
            return False
        return snapshot is not None and snapshot.has_values

    # --- внутреннее ---------------------------------------------------------

    async def _ensure_state(self, day: dt.date) -> None:
        """Получить состояние формы, если его ещё нет."""
        if self._state.is_ready:
            return
        html = await self._post({"d": _date_token(day), "t": "1"}, day)
        self._absorb_state(html)
        if not self._state.is_ready:
            raise PositionsSourceError(
                "страница открытых позиций не отдала состояние формы: разметка изменилась"
            )
        logger.info(
            "позиции: список инструментов получен, контрактов %d",
            len(self._state.instruments),
        )

    def _data_fields(self, instrument: Instrument, day: dt.date) -> dict[str, str]:
        """Поля запроса за один контракт и одну дату."""
        return {
            "__EVENTTARGET": instrument.event_target,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": self._state.viewstate,
            "__VIEWSTATEGENERATOR": self._state.generator,
            "__EVENTVALIDATION": self._state.validation,
            "ctl00$PageContent$strSearch": "",
            "ctl00$PageContent$frmTypeList": "F, C, P",
            "ctl00$PageContent$frmDateTime$CDateDay": str(day.day),
            "ctl00$PageContent$frmDateTime$CDateMonth": str(day.month),
            "ctl00$PageContent$frmDateTime$CDateYear": str(day.year),
            "ctl00$PageContent$hiddenSelectedText": instrument.selected_text,
        }

    def _absorb_state(self, html: str) -> None:
        """Забрать из ответа состояние формы для следующего обмена."""
        state = parse_page_state(html)
        if state.viewstate:
            self._state.viewstate = state.viewstate
            self._state.generator = state.generator
            self._state.validation = state.validation
        if state.instruments:
            self._state.instruments.update(state.instruments)

    async def _post(self, fields: dict[str, str], day: dt.date) -> str:
        """Один обмен с источником, с паузами и повторами."""
        if self._client is None:
            raise PositionsSourceError("клиент не инициализирован: используйте async with")

        await self._throttle()

        delay = self._settings.market_data_positions_retry_backoff_seconds
        last_error = "неизвестная причина"

        for attempt in range(1, self._settings.market_data_positions_retries + 1):
            try:
                response = await self._client.post(
                    REQUEST_URL,
                    params={"d": _date_token(day), "t": "1"},
                    data=fields,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as error:
                # У обрыва соединения текст пустой: без имени класса в
                # сообщении остаётся «сетевая ошибка: » — причина, по которой
                # нечего искать. То же правило, что у клиента ЦБ (FR-002).
                last_error = f"сетевая ошибка: {error or type(error).__name__}"
            else:
                if response.status_code == httpx.codes.OK:
                    return response.content.decode("utf-8", errors="ignore")
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
        """Пауза между пачками обращений.

        Темп задан конфигурацией, а не константой: допустимая нагрузка на чужой
        бесплатный сервер — вопрос эксплуатации и может измениться без единой
        правки логики.
        """
        size = self._settings.market_data_positions_batch_size
        pause = self._settings.market_data_positions_batch_pause_seconds
        if self._requests_made and pause and self._requests_made % size == 0:
            logger.debug("позиции: пауза %.1f с после %d обращений", pause, self._requests_made)
            await asyncio.sleep(pause)
        self._requests_made += 1


# --- разбор -------------------------------------------------------------------


def parse_page_state(html: str) -> PageState:
    """Скрытые поля формы и список инструментов из ответа."""
    soup = BeautifulSoup(html, "html.parser")

    hidden: dict[str, str] = {}
    for field_tag in soup.find_all("input", attrs={"type": "hidden"}):
        name = field_tag.get("name")
        if isinstance(name, str):
            hidden[name] = str(field_tag.get("value") or "")

    instruments: dict[str, Instrument] = {}
    for anchor in soup.select(INSTRUMENT_SELECTOR):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        match = CONTRACT_CODE_RE.search(text)
        if match is None:
            continue

        href = str(anchor.get("href") or "")
        postback = POSTBACK_RE.search(href)
        anchor_id = str(anchor.get("id") or "")
        target = postback.group(1) if postback else anchor_id.replace("_", "$")
        if not target:
            continue

        instruments.setdefault(
            match.group(1).upper(), Instrument(event_target=target, selected_text=text)
        )

    return PageState(
        viewstate=hidden.get("__VIEWSTATE", ""),
        generator=hidden.get("__VIEWSTATEGENERATOR", ""),
        validation=hidden.get("__EVENTVALIDATION", ""),
        instruments=instruments,
    )


def parse_snapshot(html: str) -> PositionSnapshot | None:
    """Открытые позиции из ответа страницы.

    ``None`` — таблицы или строки открытых позиций в ответе нет.
    """
    soup = BeautifulSoup(html, "html.parser")

    table = soup.select_one(TABLE_SELECTOR)
    if table is None:
        return None

    trade_date = parse_trade_date(soup)
    if trade_date is None:
        return None

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 6:
            continue
        title = " ".join(cells[0].get_text(" ", strip=True).lower().split())
        # Именно по названию, а не по номеру строки: порядок строк в живых
        # ответах различается, и позиционное отображение записало бы число
        # держателей позиций в столбец открытых позиций.
        if not title.startswith(OPEN_POSITION_TITLE):
            continue

        values = [_number(cell.get_text(" ", strip=True)) for cell in cells[1:5]]
        return PositionSnapshot(
            trade_date=trade_date,
            fiz_long=values[0],
            fiz_short=values[1],
            jur_long=values[2],
            jur_short=values[3],
        )

    return None


def parse_trade_date(soup: BeautifulSoup) -> dt.date | None:
    """Дата, за которую биржа отдала снимок.

    Она обязательна: ответ за другую дату не является наблюдением о
    запрошенной сессии, и без этой проверки передатирование стало бы возможным.
    """
    marker = soup.find("span", class_="text-center")
    if marker is None:
        return None
    text = marker.get_text(" ", strip=True)

    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso is not None:
        return dt.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    dotted = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if dotted is not None:
        return dt.date(int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1)))

    # Живой ответ: «Данные на 28 августа 2026».
    russian = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})", text)
    if russian is not None:
        month = RUSSIAN_MONTHS.get(russian.group(2).lower())
        if month is not None:
            return dt.date(int(russian.group(3)), month, int(russian.group(1)))

    return None


def _number(raw: str) -> Decimal | None:
    """Русский формат числа: неразрывные пробелы и запятая вместо точки."""
    cleaned = raw.replace("\xa0", "").replace(" ", "").replace(",", ".").replace("−", "-")
    return to_decimal(cleaned)


def _date_token(day: dt.date) -> str:
    return day.strftime("%Y%m%d")
