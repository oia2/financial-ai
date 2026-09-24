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
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.iss.client import IssClient, IssError
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import equity_d1, trading_calendar
from financial_ai.market_data.verification import RESULT_CONFIRMED_ABSENCE, RESULT_VALUE

logger = logging.getLogger(__name__)

# Раньше на MOEX торгов не было.
EARLIEST_DATE = dt.date(1990, 1, 1)

# Доказательство выполненной загрузки истории бумаги. Отдельный источник, а не
# котировки: история одной бумаги полноту доски за дату не подтверждает
# (FR-032d), и в расчёт полноты групп это доказательство не входит.
HISTORY_SOURCE_ID = "equity_history"

# Ключ доказательства — бумага и НАЧАЛО загруженного диапазона, дата
# доказательства — его конец. Прежний ключ ``ticker:<бумага>`` начала не хранил,
# и любая прежняя загрузка считалась загрузкой любого диапазона: повтор с более
# ранним ``--from`` не делал ни одного запроса (ревью 2026-09-24, R4). Такой ключ
# никакого начала не подтверждает и в покрытие не входит.
_SPAN_PREFIX = "range:"


def span_key(ticker: str, start: dt.date) -> str:
    return f"{_SPAN_PREFIX}{ticker}:{start.isoformat()}"


def _parse_span(work_key: str, till: dt.date) -> tuple[str, dt.date, dt.date] | None:
    if not work_key.startswith(_SPAN_PREFIX):
        return None
    ticker, _, raw = work_key.removeprefix(_SPAN_PREFIX).rpartition(":")
    try:
        return ticker, dt.date.fromisoformat(raw), till
    except ValueError:
        return None


def uncovered(
    spans: list[tuple[dt.date, dt.date]], start: dt.date, end: dt.date
) -> list[tuple[dt.date, dt.date]]:
    """Части ``[start, end]``, не покрытые доказанными диапазонами."""
    gaps: list[tuple[dt.date, dt.date]] = []
    cursor = start
    for span_from, span_till in sorted(spans):
        if span_till < cursor:
            continue
        if span_from > end:
            break
        if span_from > cursor:
            gaps.append((cursor, span_from - dt.timedelta(days=1)))
        cursor = max(cursor, span_till + dt.timedelta(days=1))
        if cursor > end:
            return gaps
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


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

    Возобновляемость — по доказательству выполненной работы: загруженный
    диапазон бумаги отмечается в таблице доказательств, и повторный запуск
    спрашивает только непокрытый остаток запрошенного диапазона — при точном
    повторе ни одного запроса. Прежде признаком служило присутствие бумаги в
    справочнике активов, и после одного обычного сбора загрузка пропускала
    бумагу целиком, не спросив её историю ни разу (FR-033h, анализ A3).

    Верхняя граница — последняя ЗАКРЫТАЯ сессия: незавершённая сегодняшняя в
    хранилище не попадает (правило модуля `ingest`).
    """
    repository = MarketDataRepository(session)
    start = resolve_start_date(settings)
    end = till or await last_closed_session(repository, settings)
    if end is None:
        logger.warning("первичная загрузка: закрытых сессий в календаре нет")
        return BackfillProgress(completed=set(), total=len(tickers))

    spans: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for work_key, till_date in await repository.work_evidence_dates(HISTORY_SOURCE_ID):
        parsed = _parse_span(work_key, till_date)
        if parsed is not None:
            spans.setdefault(parsed[0], []).append((parsed[1], parsed[2]))
    remainder = {ticker: uncovered(spans.get(ticker, []), start, end) for ticker in tickers}
    completed = {ticker for ticker, gaps in remainder.items() if not gaps}
    progress = BackfillProgress(completed=completed, total=len(tickers))
    started = dt.datetime.now(dt.UTC)

    # ОДИН идентификатор на всю загрузку, а не на каждую бумагу. Журнал
    # группирует исходы по прогону: пятьсот шесть бумаг давали пятьсот шесть
    # прогонов по одному источнику, и список последних прогонов вмещал пять
    # бумаг вместо одной загрузки (FR-052).
    run_id = str(uuid.uuid4())
    written_total = 0

    for position, ticker in enumerate(tickers, start=1):
        if ticker in completed:
            continue

        written = 0
        failed = False
        for gap_from, gap_till in remainder[ticker]:
            try:
                rows = await client.fetch_security_history(
                    ticker, gap_from.isoformat(), gap_till.isoformat(), equity_d1.COLUMNS
                )
            except IssError as error:
                # Одна недоступная бумага не должна отменять уже загруженные:
                # прерывание переживается, потеря — нет.
                logger.warning("первичная загрузка: %s не загружена (%s)", ticker, error)
                failed = True
                break

            gap_written = await _store_history(repository, ticker, rows, gap_from, gap_till)
            written += gap_written
            written_total += gap_written
            await repository.record_work_evidence(
                source_id=HISTORY_SOURCE_ID,
                session_date=gap_till,
                work_key=span_key(ticker, gap_from),
                result_kind=RESULT_VALUE if gap_written else RESULT_CONFIRMED_ABSENCE,
                reason_code="history_loaded" if gap_written else "verified_empty_history",
                origin_run_id=run_id,
            )
            # Первичная загрузка записывает свой исход наравне с остальным
            # сбором. Не ради отчётности: по этой таблице отвечают на вопрос
            # «собиралось ли что-нибудь после такого-то момента», и молчаливая
            # запись мимо неё означала бы «ничего не собирали» при переписанной
            # истории.
            await _record_backfill(repository, run_id, written_total, started)
            await session.commit()
        if failed:
            continue

        completed.add(ticker)
        logger.info(
            "первичная загрузка: %s — %d наблюдений (%d из %d)",
            ticker,
            written,
            position,
            progress.total,
        )

    return progress


async def _record_backfill(
    repository: MarketDataRepository, run_id: str, written: int, started: dt.datetime
) -> None:
    """Обновить исход первичной загрузки.

    Исход один на всю загрузку, и с каждой бумагой он дополняется: число
    наблюдений растёт, отметка завершения сдвигается. Бумага — не прогон, и
    заводить идентификатор на каждую значило бы показывать в журнале бумаги
    вместо загрузок (FR-052).
    """
    await repository.record_run(
        run_id=run_id,
        source_id=equity_d1.SOURCE_ID,
        status="ok",
        started_at=started,
        finished_at=dt.datetime.now(dt.UTC),
        session_date=None,
        rows_written=written,
        trigger="backfill",
    )


async def last_closed_session(
    repository: MarketDataRepository, settings: Settings
) -> dt.date | None:
    """Последняя закрытая сессия календаря: сегодняшняя — только после порога."""
    from financial_ai.market_data.advance import session_is_closed

    calendar = TradingCalendar(repository)
    latest = await calendar.latest_session(moscow_today())
    if latest is None:
        return None
    for day in reversed(await calendar.window(latest, 2)):
        if session_is_closed(day, settings):
            return day
    return None


async def _store_history(
    repository: MarketDataRepository,
    ticker: str,
    rows: list[dict[str, object]],
    start: dt.date,
    end: dt.date,
) -> int:
    """Разложить историю одной бумаги по датам и сохранить — в ``[start, end]``."""
    by_date: dict[dt.date, dict[str, object]] = {}
    for row in rows:
        parsed = trading_calendar.parse_date(row.get("TRADEDATE"))
        if parsed is not None and start <= parsed <= end:
            by_date[parsed] = row

    if not by_date:
        return 0

    last_date = max(by_date)
    # Ключ — СУЩНОСТЬ, а не имя: переименованная бумага иначе заводит вторую,
    # и история, ради которой загрузка и делается, начинается с нуля (FR-048).
    aliases = await repository.aliases_on(last_date)
    asset_id = aliases.get(ticker, equity_d1.asset_id_for(ticker))
    series_id = asset_id.replace(equity_d1.ASSET_PREFIX, equity_d1.SERIES_PREFIX, 1)
    await repository.upsert_asset(
        asset_id, asset_id.removeprefix(equity_d1.ASSET_PREFIX), last_date
    )
    await repository.upsert_price_series(series_id, asset_id, last_date)

    bars = []
    for day, row in sorted(by_date.items()):
        bars.extend(equity_d1.rows_to_bars([{**row, "SECID": ticker}], day, aliases))

    return await repository.upsert_daily_bars(bars)
