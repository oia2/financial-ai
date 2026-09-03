"""Первичная загрузка истории.

Здесь перебор идёт **по бумагам**, а не по датам, — в отличие от ежедневного
добора. Это не непоследовательность, а разные задачи: за всю историю одной
бумаги биржа отдаёт тысячи строк одним запросом, тогда как ежедневному добору
нужна одна дата по всем бумагам сразу.

Загрузка выполняется частями и переживает прерывание: при повторном запуске она
продолжается с места остановки, а не начинается заново. Иначе прерванная на
третьем часу загрузка означала бы три потерянных часа.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data.iss.client import IssClient, IssError
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import equity_d1, trading_calendar

logger = logging.getLogger(__name__)

# Раньше на MOEX торгов не было.
EARLIEST_DATE = dt.date(1990, 1, 1)


class BackfillProgress:
    """Что уже загружено. Основа возобновляемости."""

    def __init__(self, completed: set[str], total: int) -> None:
        self.completed = completed
        self.total = total

    @property
    def remaining(self) -> int:
        return max(0, self.total - len(self.completed))


def resolve_start_date(settings: Settings) -> dt.date:
    """Начальная дата загрузки.

    Пустая настройка означает «вся доступная история»: история с биржи
    бесплатна, а повторная докачка поверх работающей системы обойдётся дороже
    первой.
    """
    raw = settings.market_data_backfill_from.strip()
    if not raw:
        return EARLIEST_DATE
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "некорректная начальная дата загрузки %r, берётся вся доступная история", raw
        )
        return EARLIEST_DATE


async def backfill_calendar(session: AsyncSession, settings: Settings, client: IssClient) -> int:
    """Заполнить календарь на всю глубину.

    Календарь идёт первым: пока неизвестно, какие дни были торговыми,
    остальное не имеет смысла.
    """
    repository = MarketDataRepository(session)
    added = await trading_calendar.sync_trading_calendar(
        client,
        repository,
        settings.market_data_calendar_proxy_security,
        date_from=resolve_start_date(settings),
    )
    await session.commit()
    return added


async def backfill_equity(
    session: AsyncSession,
    settings: Settings,
    client: IssClient,
    tickers: list[str],
    till: dt.date | None = None,
) -> BackfillProgress:
    """Загрузить историю котировок по каждой бумаге.

    Возобновляемость: бумага, у которой уже есть наблюдения, пропускается.
    Отметка хранится в самих данных — отдельного файла состояния не нужно, и
    он не может разойтись с тем, что реально загружено.
    """
    repository = MarketDataRepository(session)
    start = resolve_start_date(settings)
    end = till or dt.date.today()

    completed = await repository.tickers_with_history()
    progress = BackfillProgress(completed=completed, total=len(tickers))

    for position, ticker in enumerate(tickers, start=1):
        if ticker in completed:
            continue

        try:
            rows = await client.fetch_security_history(
                ticker, start.isoformat(), end.isoformat(), equity_d1.COLUMNS
            )
        except IssError as error:
            # Одна недоступная бумага не должна отменять уже загруженные:
            # прерывание переживается, потеря — нет.
            logger.warning("первичная загрузка: %s не загружена (%s)", ticker, error)
            continue

        written = await _store_history(repository, ticker, rows)
        await session.commit()

        completed.add(ticker)
        logger.info(
            "первичная загрузка: %s — %d наблюдений (%d из %d)",
            ticker,
            written,
            position,
            progress.total,
        )

    return progress


async def _store_history(
    repository: MarketDataRepository, ticker: str, rows: list[dict[str, object]]
) -> int:
    """Разложить историю одной бумаги по датам и сохранить."""
    by_date: dict[dt.date, dict[str, object]] = {}
    for row in rows:
        parsed = trading_calendar.parse_date(row.get("TRADEDATE"))
        if parsed is not None:
            by_date[parsed] = row

    if not by_date:
        return 0

    asset_id = equity_d1.asset_id_for(ticker)
    series_id = equity_d1.price_series_id_for(ticker)
    last_date = max(by_date)
    await repository.upsert_asset(asset_id, ticker, last_date)
    await repository.upsert_price_series(series_id, asset_id, last_date)

    bars = []
    for day, row in sorted(by_date.items()):
        bars.extend(equity_d1.rows_to_bars([{**row, "SECID": ticker}], day))

    return await repository.upsert_daily_bars(bars)
