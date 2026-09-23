"""Источник торгового календаря.

Перенесён из `pipelines/moex_trading_calendar_sync/cli.py` (`MR-MASTER-DRO`,
`f07295e`); запись идёт в PostgreSQL вместо CSV.

Календарь строится не по справочнику праздников, а по факту: запрашивается
история торгов опорной ликвидной бумаги, и даты, в которые она торговалась, и
есть торговые сессии. Так переносы рабочих дней и внеплановые остановки
попадают в календарь сами.
"""

from __future__ import annotations

import datetime as dt
import logging

from financial_ai.market_data.calendar import moscow_today
from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository

logger = logging.getLogger(__name__)

SOURCE_ID = "trading_calendar"
COLUMNS = ("TRADEDATE",)

# Раньше на MOEX торгов не было — нижняя граница запроса истории.
EARLIEST_DATE = dt.date(1990, 1, 1)


async def sync_trading_calendar(
    client: IssClient,
    repository: MarketDataRepository,
    proxy_security: str,
    date_from: dt.date | None = None,
    date_till: dt.date | None = None,
) -> int:
    """Пополнить календарь торговыми сессиями опорной бумаги.

    Возвращает число добавленных сессий. Уже известные не дублируются.
    """
    if date_from is not None:
        start = date_from
    else:
        # Daily refreshes need only a bounded overlap before the saved tail.
        # Using today's date here would strand the calendar after downtime;
        # anchoring to the last saved session still fetches the entire gap.
        latest = await repository.latest_trading_session()
        start = latest - dt.timedelta(days=14) if latest is not None else EARLIEST_DATE
    # Московская дата, а не дата пояса процесса: в контейнере UTC, и с 00:00
    # до 03:00 МСК граница запроса отставала на сутки (FR-040a).
    end = date_till or moscow_today()

    rows = await client.fetch_security_history(
        proxy_security, start.isoformat(), end.isoformat(), COLUMNS
    )

    dates: list[dt.date] = []
    for row in rows:
        raw = row.get("TRADEDATE")
        parsed = parse_date(raw)
        if parsed is not None:
            dates.append(parsed)

    added = await repository.add_trading_sessions(sorted(set(dates)))
    logger.info(
        "календарь: получено %d дат по бумаге %s, добавлено новых сессий %d",
        len(dates),
        proxy_security,
        added,
    )
    return added


def parse_date(raw: object) -> dt.date | None:
    if isinstance(raw, dt.date):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return dt.date.fromisoformat(raw.strip()[:10])
        except ValueError:
            logger.warning("календарь: не удалось разобрать дату %r", raw)
    return None
