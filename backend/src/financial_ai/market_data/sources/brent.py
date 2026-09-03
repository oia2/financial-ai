"""Brent: непрерывный ряд по фронтальному контракту.

Нефть торгуется отдельными контрактами с разными сроками (`BR-9.26` — сентябрь
2026), поэтому «цена Brent» — не готовый ряд, а результат склейки: нужно решить,
какой контракт считается текущим, и когда переключаться на следующий.

**Здесь склейка проще, чем в исследовательском репозитории.** Оригинал
(`pipelines/br_continuous_history_sync/`, 721 строка) строит таблицу контрактов и
**оценивает** правило переката по объёмам торгов. Здесь фронтальным считается
контракт с ближайшим неистёкшим сроком — правило детерминированное и
объяснимое, но другое.

Следствие, которое нельзя замалчивать: **вблизи дат переката значения разойдутся
с исследовательским рядом.** Поэтому ряд назван `BRENT_FRONT`, а не `BRENT`:
если однажды в наборе появится ряд оригинальной склейки, их будет видно
раздельно, а не молча перепутано. Перед тем как модель начнёт опираться на этот
ряд, значения нужно сверить с исследовательскими.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import to_decimal

logger = logging.getLogger(__name__)

SOURCE_ID = "brent"
SERIES_ID = "BRENT_FRONT"

COLUMNS = ("SECID", "SHORTNAME", "TRADEDATE", "CLOSE")

ENGINE = "futures"
MARKET = "forts"

# Короткое имя контракта: BR-9.26 — сентябрь 2026 года.
SHORTNAME_RE = re.compile(r"^BR-(\d{1,2})\.(\d{2})$")


@dataclass(frozen=True, slots=True)
class Contract:
    """Один фьючерсный контракт на дату."""

    secid: str
    expiry: dt.date
    close: Decimal | None


async def sync_brent(
    client: IssClient, repository: MarketDataRepository, session_date: dt.date
) -> int:
    """Собрать цену фронтального контракта за одну торговую сессию."""
    rows = await client.fetch_session_rows_for(
        session_date.isoformat(), COLUMNS, engine=ENGINE, market=MARKET
    )
    contract = select_front_contract(rows, session_date)
    if contract is None:
        logger.warning("Brent за %s: подходящего контракта не нашлось", session_date)
        return 0

    logger.info(
        "Brent за %s: фронтальный контракт %s (срок %s)",
        session_date,
        contract.secid,
        contract.expiry,
    )
    return await repository.upsert_global_values(SERIES_ID, {session_date: contract.close})


def select_front_contract(rows: list[dict[str, object]], session_date: dt.date) -> Contract | None:
    """Выбрать контракт с ближайшим неистёкшим сроком.

    Истёкшие контракты отбрасываются: если бы они оставались, ряд «залипал» бы
    на последнем контракте после его экспирации и перестал бы отражать рынок.
    """
    contracts = [c for c in _parse_contracts(rows) if c.expiry >= session_date]
    if not contracts:
        return None
    # Вторичный ключ — идентификатор: при равных сроках порядок не должен
    # зависеть от порядка обхода ответа биржи.
    return min(contracts, key=lambda c: (c.expiry, c.secid))


def _parse_contracts(rows: list[dict[str, object]]) -> list[Contract]:
    out: list[Contract] = []
    for row in rows:
        secid = row.get("SECID")
        expiry = _expiry_from_shortname(row.get("SHORTNAME"))
        if not isinstance(secid, str) or expiry is None:
            continue
        out.append(
            Contract(secid=secid.strip().upper(), expiry=expiry, close=to_decimal(row.get("CLOSE")))
        )
    return out


def _expiry_from_shortname(raw: object) -> dt.date | None:
    """Срок контракта из короткого имени `BR-9.26`.

    Берётся **последний** день месяца поставки, а не первый. Разница не
    косметическая: с первым числом контракт текущего месяца считался бы уже
    истёкшим, и ряд перескакивал бы на следующий контракт в начале каждого
    месяца — то есть ровно тогда, когда текущий ещё активно торгуется.

    Точный день экспирации при этом не нужен и не угадывается: в ответе биржи
    за дату присутствуют только те контракты, которые в этот день торговались,
    поэтому по-настоящему истёкшие в выборку не попадают вовсе. Фильтр по сроку
    здесь — страховка, а не основной механизм.
    """
    if not isinstance(raw, str):
        return None
    match = SHORTNAME_RE.match(raw.strip())
    if match is None:
        return None
    month, year = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return _month_end(2000 + year, month)


def _month_end(year: int, month: int) -> dt.date:
    """Последний день месяца."""
    if month == 12:
        return dt.date(year, 12, 31)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)
