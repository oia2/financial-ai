"""Запись и чтение рыночных данных.

Три правила, нарушение которых искажает данные необратимо и незаметно:

1. запись идемпотентна по ключу — повторный сбор не создаёт дубликатов;
2. пропуск хранится как ``None`` и нулём не заменяется;
3. ``Decimal`` на всём пути — ``float`` в этом модуле не используется.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.models import (
    AssetSector,
    DividendEvent,
    EquityAggregate,
    EquityDailyBar,
    FuturesPosition,
    GlobalDailySeries,
    IngestRun,
    MarketAsset,
    PriceSeries,
    TradingSession,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DailyBar:
    """Одно дневное наблюдение по активу."""

    asset_id: str
    price_series_id: str
    session_date: dt.date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None

    def revision(self) -> str:
        """Отпечаток значений — для обнаружения переиздания биржей."""
        parts = [
            str(v) if v is not None else ""
            for v in (self.open, self.high, self.low, self.close, self.volume)
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class AggregateRow:
    """Дневной агрегат торгов по активу."""

    asset_id: str
    price_series_id: str
    session_date: dt.date
    value: Decimal | None
    num_trades: Decimal | None
    waprice: Decimal | None


@dataclass(frozen=True, slots=True)
class PositionRow:
    """Позиции участников по фьючерсам на актив."""

    asset_id: str
    session_date: dt.date
    fiz_long: Decimal | None
    fiz_short: Decimal | None
    jur_long: Decimal | None
    jur_short: Decimal | None


@dataclass(frozen=True, slots=True)
class DividendRow:
    """Одно дивидендное событие."""

    asset_id: str
    record_date: dt.date
    declared_date: dt.date | None
    last_buy_date: dt.date | None
    payment_date: dt.date | None
    value: Decimal | None


class MarketDataRepository:
    """Доступ к собранным рыночным данным."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- торговые сессии ---------------------------------------------------

    async def add_trading_sessions(self, dates: list[dt.date]) -> int:
        """Добавить торговые сессии. Уже известные не дублируются."""
        if not dates:
            return 0
        statement = (
            insert(TradingSession)
            .values([{"session_date": d} for d in dates])
            .on_conflict_do_nothing(index_elements=["session_date"])
        )
        # rowcount у INSERT ... ON CONFLICT DO NOTHING даёт число фактически
        # вставленных строк — то есть новых сессий.
        result = await self._session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    async def is_trading_session(self, session_date: dt.date) -> bool:
        found = await self._session.scalar(
            select(TradingSession.session_date).where(TradingSession.session_date == session_date)
        )
        return found is not None

    async def latest_trading_session(self, not_after: dt.date | None = None) -> dt.date | None:
        statement = select(TradingSession.session_date).order_by(TradingSession.session_date.desc())
        if not_after is not None:
            statement = statement.where(TradingSession.session_date <= not_after)
        return await self._session.scalar(statement.limit(1))

    async def next_trading_session(self, after: dt.date) -> dt.date | None:
        """Следующая торговая сессия — та, в которую исполнится сделка."""
        return await self._session.scalar(
            select(TradingSession.session_date)
            .where(TradingSession.session_date > after)
            .order_by(TradingSession.session_date)
            .limit(1)
        )

    async def previous_sessions(self, asof: dt.date, count: int) -> list[dt.date]:
        """Последние ``count`` торговых сессий, включая ``asof``.

        Отсчёт идёт по ТОРГОВЫМ сессиям, а не по календарным дням: на
        новогодних каникулах разница почти в две недели.
        """
        rows = await self._session.scalars(
            select(TradingSession.session_date)
            .where(TradingSession.session_date <= asof)
            .order_by(TradingSession.session_date.desc())
            .limit(count)
        )
        return sorted(rows.all())

    # --- активы и ценовые ряды --------------------------------------------

    async def upsert_asset(self, asset_id: str, ticker: str, session_date: dt.date) -> None:
        statement = (
            insert(MarketAsset)
            .values(
                asset_id=asset_id,
                ticker=ticker,
                first_seen_date=session_date,
                last_seen_date=session_date,
            )
            .on_conflict_do_update(
                index_elements=["asset_id"],
                set_={
                    "ticker": ticker,
                    "last_seen_date": session_date,
                },
            )
        )
        await self._session.execute(statement)

    async def upsert_price_series(
        self, price_series_id: str, asset_id: str, session_date: dt.date
    ) -> None:
        statement = (
            insert(PriceSeries)
            .values(
                price_series_id=price_series_id,
                asset_id=asset_id,
                first_date=session_date,
                last_date=session_date,
            )
            .on_conflict_do_update(
                index_elements=["price_series_id"],
                set_={"last_date": session_date},
            )
        )
        await self._session.execute(statement)

    # --- дневные наблюдения ------------------------------------------------

    async def upsert_daily_bars(self, bars: list[DailyBar]) -> int:
        """Сохранить наблюдения идемпотентно.

        Переиздание биржей уже сохранённого значения не проходит молча:
        расхождение отпечатков попадает в журнал.
        """
        if not bars:
            return 0

        await self._warn_on_revisions(bars)

        payload = [
            {
                "price_series_id": bar.price_series_id,
                "session_date": bar.session_date,
                "asset_id": bar.asset_id,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "source_revision": bar.revision(),
            }
            for bar in bars
        ]
        statement = insert(EquityDailyBar).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["price_series_id", "session_date"],
            set_={
                "asset_id": statement.excluded.asset_id,
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "source_revision": statement.excluded.source_revision,
            },
        )
        await self._session.execute(statement)
        return len(payload)

    async def _warn_on_revisions(self, bars: list[DailyBar]) -> None:
        """Сообщить о переиздании ранее сохранённых значений (FR-015)."""
        keys = {(bar.price_series_id, bar.session_date) for bar in bars}
        existing = await self._session.execute(
            select(
                EquityDailyBar.price_series_id,
                EquityDailyBar.session_date,
                EquityDailyBar.source_revision,
            ).where(
                EquityDailyBar.price_series_id.in_({k[0] for k in keys}),
                EquityDailyBar.session_date.in_({k[1] for k in keys}),
            )
        )
        stored = {(row[0], row[1]): row[2] for row in existing}
        for bar in bars:
            previous = stored.get((bar.price_series_id, bar.session_date))
            if previous is not None and previous != bar.revision():
                logger.warning(
                    "биржа переиздала значение за закрытую дату: %s %s (было %s, стало %s)",
                    bar.price_series_id,
                    bar.session_date,
                    previous,
                    bar.revision(),
                )

    async def tickers_with_history(self) -> set[str]:
        """Бумаги, по которым наблюдения уже есть.

        Основа возобновляемости первичной загрузки: отметка хранится в самих
        данных, а не в отдельном файле состояния, который мог бы с ними
        разойтись.
        """
        rows = await self._session.scalars(select(MarketAsset.ticker))
        return set(rows.all())

    async def count_daily_bars(self, session_date: dt.date) -> int:
        rows = await self._session.scalars(
            select(EquityDailyBar.price_series_id).where(
                EquityDailyBar.session_date == session_date
            )
        )
        return len(rows.all())

    async def daily_bars_for_window(self, sessions: list[dt.date]) -> list[EquityDailyBar]:
        if not sessions:
            return []
        rows = await self._session.scalars(
            select(EquityDailyBar)
            .where(EquityDailyBar.session_date.in_(sessions))
            .order_by(EquityDailyBar.asset_id, EquityDailyBar.session_date)
        )
        return list(rows.all())

    # --- глобальные ряды ---------------------------------------------------

    async def upsert_global_values(
        self, series_id: str, values: dict[dt.date, Decimal | None]
    ) -> int:
        if not values:
            return 0
        payload = [
            {"series_id": series_id, "session_date": d, "value": v}
            for d, v in sorted(values.items())
        ]
        statement = insert(GlobalDailySeries).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["series_id", "session_date"],
            set_={"value": statement.excluded.value},
        )
        await self._session.execute(statement)
        return len(payload)

    async def global_values_for_window(self, sessions: list[dt.date]) -> list[GlobalDailySeries]:
        if not sessions:
            return []
        rows = await self._session.scalars(
            select(GlobalDailySeries)
            .where(GlobalDailySeries.session_date.in_(sessions))
            .order_by(GlobalDailySeries.series_id, GlobalDailySeries.session_date)
        )
        return list(rows.all())

    # --- агрегаты, позиции, секторы ----------------------------------------

    async def upsert_aggregates(self, rows: list[AggregateRow]) -> int:
        if not rows:
            return 0
        payload = [
            {
                "price_series_id": r.price_series_id,
                "session_date": r.session_date,
                "asset_id": r.asset_id,
                "value": r.value,
                "num_trades": r.num_trades,
                "waprice": r.waprice,
            }
            for r in rows
        ]
        statement = insert(EquityAggregate).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["price_series_id", "session_date"],
            set_={
                "asset_id": statement.excluded.asset_id,
                "value": statement.excluded.value,
                "num_trades": statement.excluded.num_trades,
                "waprice": statement.excluded.waprice,
            },
        )
        await self._session.execute(statement)
        return len(payload)

    async def upsert_positions(self, rows: list[PositionRow]) -> int:
        """Сохранить позиции.

        Дата наблюдения приходит из аргумента и никогда не подменяется датой
        получения: передатирование опоздавших данных запрещено.
        """
        if not rows:
            return 0
        payload = [
            {
                "asset_id": r.asset_id,
                "session_date": r.session_date,
                "fiz_long": r.fiz_long,
                "fiz_short": r.fiz_short,
                "jur_long": r.jur_long,
                "jur_short": r.jur_short,
            }
            for r in rows
        ]
        statement = insert(FuturesPosition).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id", "session_date"],
            set_={
                "fiz_long": statement.excluded.fiz_long,
                "fiz_short": statement.excluded.fiz_short,
                "jur_long": statement.excluded.jur_long,
                "jur_short": statement.excluded.jur_short,
            },
        )
        await self._session.execute(statement)
        return len(payload)

    async def positions_for_window(self, sessions: list[dt.date]) -> list[FuturesPosition]:
        if not sessions:
            return []
        rows = await self._session.scalars(
            select(FuturesPosition)
            .where(FuturesPosition.session_date.in_(sessions))
            .order_by(FuturesPosition.asset_id, FuturesPosition.session_date)
        )
        return list(rows.all())

    async def aggregates_for_window(self, sessions: list[dt.date]) -> list[EquityAggregate]:
        if not sessions:
            return []
        rows = await self._session.scalars(
            select(EquityAggregate)
            .where(EquityAggregate.session_date.in_(sessions))
            .order_by(EquityAggregate.asset_id, EquityAggregate.session_date)
        )
        return list(rows.all())

    async def upsert_sectors(self, sectors: dict[str, str | None]) -> int:
        if not sectors:
            return 0
        payload = [{"asset_id": a, "sector": s} for a, s in sorted(sectors.items())]
        statement = insert(AssetSector).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id"], set_={"sector": statement.excluded.sector}
        )
        await self._session.execute(statement)
        return len(payload)

    async def sectors(self) -> dict[str, str | None]:
        rows = await self._session.execute(select(AssetSector.asset_id, AssetSector.sector))
        return {row[0]: row[1] for row in rows}

    async def upsert_dividends(self, rows: list[DividendRow]) -> int:
        if not rows:
            return 0
        payload = [
            {
                "asset_id": r.asset_id,
                "record_date": r.record_date,
                "declared_date": r.declared_date,
                "last_buy_date": r.last_buy_date,
                "payment_date": r.payment_date,
                "value": r.value,
            }
            for r in rows
        ]
        statement = insert(DividendEvent).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id", "record_date"],
            set_={
                "declared_date": statement.excluded.declared_date,
                "last_buy_date": statement.excluded.last_buy_date,
                "payment_date": statement.excluded.payment_date,
                "value": statement.excluded.value,
            },
        )
        await self._session.execute(statement)
        return len(payload)

    async def dividends_for_assets(self, asset_ids: list[str]) -> list[DividendEvent]:
        if not asset_ids:
            return []
        rows = await self._session.scalars(
            select(DividendEvent)
            .where(DividendEvent.asset_id.in_(asset_ids))
            .order_by(DividendEvent.asset_id, DividendEvent.record_date)
        )
        return list(rows.all())

    # --- журнал прогонов ---------------------------------------------------

    async def record_run(
        self,
        run_id: str,
        source_id: str,
        status: str,
        started_at: dt.datetime,
        finished_at: dt.datetime | None = None,
        session_date: dt.date | None = None,
        rows_written: int = 0,
        failure_reason: str | None = None,
    ) -> None:
        statement = (
            insert(IngestRun)
            .values(
                run_id=run_id,
                source_id=source_id,
                session_date=session_date,
                status=status,
                failure_reason=failure_reason,
                rows_written=rows_written,
                started_at=started_at,
                finished_at=finished_at,
            )
            .on_conflict_do_update(
                constraint="uq_ingest_run_source",
                set_={
                    "status": status,
                    "failure_reason": failure_reason,
                    "rows_written": rows_written,
                    "finished_at": finished_at,
                },
            )
        )
        await self._session.execute(statement)

    async def runs_for_session(self, session_date: dt.date) -> list[IngestRun]:
        rows = await self._session.scalars(
            select(IngestRun)
            .where(IngestRun.session_date == session_date)
            .order_by(IngestRun.started_at)
        )
        return list(rows.all())
