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

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from financial_ai.market_data.models import (
    UNKNOWN_CONTRACT,
    AssetAlias,
    AssetFuturesLink,
    AssetSector,
    DividendEvent,
    EquityAggregate,
    EquityDailyBar,
    FuturesPosition,
    GlobalDailySeries,
    IngestRun,
    MarketAsset,
    PriceSeries,
    SessionSkip,
    TradingSession,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GroupCoverageRaw:
    """Сырые числа покрытия одной группы. Тип, а не словарь: значения разные."""

    sessions_covered: int | None
    period_from: dt.date | None
    period_till: dt.date | None
    rows_total: int
    rows_with_values: int


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
    # Каким семейством контрактов наблюдение собрано. Часть ключа: повторный
    # сбор той же даты другим контрактом не должен затирать прежнее молча.
    contract_code: str = UNKNOWN_CONTRACT


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

    async def earliest_observed_session(self) -> dt.date | None:
        """Самая ранняя сессия, за которую ЕСТЬ наблюдения.

        Граница листания календаря. Не самая ранняя сессия календаря: тот
        знает торги с 2013 года, а собранного там нет и не предполагается —
        листать туда значит листать пустоту.
        """
        return await self._session.scalar(
            select(EquityDailyBar.session_date).order_by(EquityDailyBar.session_date).limit(1)
        )

    async def latest_observed_session(self) -> dt.date | None:
        """Самая поздняя сессия, за которую ЕСТЬ котировки.

        По ней берётся состав доски для сверки связей: «какие бумаги
        торгуются» и «с какого дня связь подтверждена» — разные вопросы, и
        ответ на первый не обязан датировать второй (FR-049).
        """
        return await self._session.scalar(
            select(EquityDailyBar.session_date)
            .order_by(EquityDailyBar.session_date.desc())
            .limit(1)
        )

    async def next_trading_session(self, after: dt.date) -> dt.date | None:
        """Следующая торговая сессия — та, в которую исполнится сделка."""
        return await self._session.scalar(
            select(TradingSession.session_date)
            .where(TradingSession.session_date > after)
            .order_by(TradingSession.session_date)
            .limit(1)
        )

    async def sessions_between(self, date_from: dt.date, date_till: dt.date) -> list[dt.date]:
        """Торговые сессии в отрезке, по возрастанию.

        Нужно календарю раздела: он показывает факт, а факт — это состоявшиеся
        торги. Дни, которых здесь нет, торговыми не были.
        """
        rows = await self._session.scalars(
            select(TradingSession.session_date)
            .where(
                TradingSession.session_date >= date_from,
                TradingSession.session_date <= date_till,
            )
            .order_by(TradingSession.session_date)
        )
        return list(rows.all())

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

    async def update_isins(self, isins: dict[str, str]) -> int:
        """Проставить устойчивые идентификаторы известным бумагам.

        Тикер — имя на период, ISIN — сама бумага. Без якоря переименование
        выглядит появлением новой бумаги и молча рвёт связь с фьючерсом
        (spec 008, FR-018).
        """
        if not isins:
            return 0

        updated = 0
        for asset_id, isin in isins.items():
            touched = await self._session.scalars(
                update(MarketAsset)
                .where(MarketAsset.asset_id == asset_id, MarketAsset.isin.is_distinct_from(isin))
                .values(isin=isin)
                .returning(MarketAsset.asset_id)
            )
            updated += len(touched.all())
        return updated

    async def update_lot_sizes(self, lots: dict[str, int]) -> int:
        """Проставить размеры лотов известным активам.

        Активы, которых в хранилище нет, не заводятся: справочник дополняет уже
        собранное, а не подменяет собой сбор.
        """
        if not lots:
            return 0

        updated = 0
        for asset_id, lot in lots.items():
            touched = await self._session.scalars(
                update(MarketAsset)
                .where(MarketAsset.asset_id == asset_id)
                .values(lot_size=lot)
                .returning(MarketAsset.asset_id)
            )
            updated += len(touched.all())

        return updated

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
                "contract_code": r.contract_code,
                "fiz_long": r.fiz_long,
                "fiz_short": r.fiz_short,
                "jur_long": r.jur_long,
                "jur_short": r.jur_short,
            }
            for r in rows
        ]
        statement = insert(FuturesPosition).values(payload)
        statement = statement.on_conflict_do_update(
            index_elements=["asset_id", "session_date", "contract_code"],
            set_={
                "fiz_long": statement.excluded.fiz_long,
                "fiz_short": statement.excluded.fiz_short,
                "jur_long": statement.excluded.jur_long,
                "jur_short": statement.excluded.jur_short,
            },
        )
        await self._session.execute(statement)
        return len(payload)

    async def assets_with_positions(self, session_date: dt.date) -> set[str]:
        """Активы, по которым за эту сессию уже есть непустая строка.

        Нужно источнику позиций: его единица обращения — «инструмент и дата»,
        и «сессия собрана» не означает «все инструменты собраны». Без этого
        повторный догон стоил бы столько же, сколько первый (FR-024c).

        Пустые строки не в счёт: строка без значений — это не собранные данные,
        и запрашивать пару заново как раз нужно.
        """
        rows = await self._session.scalars(
            select(FuturesPosition.asset_id).where(
                FuturesPosition.session_date == session_date,
                _positions_filled(),
            )
        )
        return set(rows.all())

    async def first_position_dates(self) -> dict[str, dt.date]:
        """Первая сессия со значениями по каждому активу.

        Отметка первой доступной даты инструмента **выводится из данных**, а не
        хранится отдельно: состояние в стороне от данных может с ними
        разойтись — то же правило, что и у прогресса первичной загрузки.
        """
        rows = await self._session.execute(
            select(FuturesPosition.asset_id, func.min(FuturesPosition.session_date))
            .where(_positions_filled())
            .group_by(FuturesPosition.asset_id)
        )
        return {asset_id: contract for asset_id, contract in rows.all()}  # noqa: C416  # type: ignore[arg-type]

    async def positions_for_window(self, sessions: list[dt.date]) -> list[FuturesPosition]:
        if not sessions:
            return []
        rows = await self._session.scalars(
            select(FuturesPosition)
            .where(FuturesPosition.session_date.in_(sessions))
            # Контракт входит в порядок: он часть ключа наблюдения, и без него
            # порядок строк одной бумаги за одну дату зависел бы от плана
            # запроса (FR-051).
            .order_by(
                FuturesPosition.asset_id,
                FuturesPosition.contract_code,
                FuturesPosition.session_date,
            )
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
        trigger: str = "daily",
        period_from: dt.date | None = None,
        period_till: dt.date | None = None,
    ) -> None:
        # Период по умолчанию — одна сессия: так ведёт себя всякий посессионный
        # источник. Источник с выборкой за диапазон передаёт период явно, иначе
        # он выглядел бы несобравшим всё, кроме последней сессии.
        if period_from is None and period_till is None and session_date is not None:
            period_from = period_till = session_date

        statement = (
            insert(IngestRun)
            .values(
                run_id=run_id,
                source_id=source_id,
                session_date=session_date,
                status=status,
                trigger=trigger,
                period_from=period_from,
                period_till=period_till,
                failure_reason=failure_reason,
                rows_written=rows_written,
                started_at=started_at,
                finished_at=finished_at,
            )
            .on_conflict_do_update(
                constraint="uq_ingest_run_source",
                set_={
                    "status": status,
                    "trigger": trigger,
                    "failure_reason": failure_reason,
                    "rows_written": rows_written,
                    "finished_at": finished_at,
                    "period_from": period_from,
                    "period_till": period_till,
                },
            )
        )
        await self._session.execute(statement)

    # --- покрытие по группам (spec 005) ------------------------------------

    async def sessions_with_observations(
        self,
        model: type,
        session_column: str,
        value_columns: tuple[str, ...],
        sessions: list[dt.date],
        key_column: str | None = None,
        keys: tuple[str, ...] | None = None,
    ) -> set[dt.date]:
        """Сессии окна, за которые в таблице группы есть непустые наблюдения.

        Полнота меряется данными, а не записями о прогонах. Для источников,
        забираемых одним запросом за весь период, запись о прогоне ставится на
        одну дату — конец периода, — и счёт по журналу объявлял бы пустыми
        сотни сессий, данные за которые лежат рядом.

        Метод намеренно **не знает о группах**: модель и столбцы приходят
        снаружи, как и у `group_coverage`.
        """
        if not sessions:
            return set()

        column = getattr(model, session_column)
        filled = or_(*(getattr(model, name).is_not(None) for name in value_columns))
        statement = select(column).where(column.in_(sessions), filled)

        # Отбор по источнику. Пустой набор имён означает «этому источнику в
        # таблице не принадлежит ничего»: такой источник закрывается только
        # журналом прогонов, и молча брать за него чужие ряды нельзя (FR-047).
        if key_column is not None and keys is not None:
            if not keys:
                return set()
            key = getattr(model, key_column)
            statement = statement.where(or_(*(key.startswith(prefix) for prefix in keys)))

        rows = await self._session.scalars(statement.distinct())
        return set(rows.all())

    async def sessions_left_unfinished(
        self, sessions: list[dt.date], source_id: str
    ) -> set[dt.date]:
        """Сессии окна, у которых ПОСЛЕДНИЙ исход источника — «прервано».

        Прерванный сбор успевает записать собранное, и наблюдение закрывало бы
        сессию (FR-047) вопреки отдельному исходу: остановка после первого
        инструмента из двух оставляла строку, и строка объявляла день собранным
        (FR-050).

        Берётся именно ПОСЛЕДНИЙ исход: сессия, остановленная однажды и
        добранная следующим прогоном, незакрытой не остаётся.
        """
        if not sessions:
            return set()

        rows = await self._session.execute(
            select(IngestRun.session_date, IngestRun.status)
            .where(
                IngestRun.session_date.in_(sessions),
                IngestRun.source_id == source_id,
            )
            .distinct(IngestRun.session_date)
            .order_by(IngestRun.session_date, IngestRun.started_at.desc(), IngestRun.id.desc())
        )
        return {day for day, status in rows.all() if status == "stopped" and day is not None}

    async def group_coverage(
        self,
        model: type,
        session_column: str | None,
        value_columns: tuple[str, ...],
        sessions: list[dt.date] | None,
    ) -> GroupCoverageRaw:
        """Покрытие и наполненность одной группы наблюдений.

        Метод намеренно **не знает о группах**: модель, столбец сессии и
        столбцы значений приходят снаружи. Иначе хранилище пришлось бы править
        при каждом изменении состава групп.

        Считаются два разных числа: сколько сессий покрыто и сколько строк
        содержат хоть одно значение. Дефект позиций жил ровно в зазоре между
        ними — покрытие полное, значений нет.
        """
        filled = or_(*(getattr(model, name).is_not(None) for name in value_columns))

        if session_column is None:
            # Справочник текущего состояния: оси сессий нет, окна тоже.
            total = await self._session.scalar(select(func.count()).select_from(model)) or 0
            with_values = (
                await self._session.scalar(select(func.count()).select_from(model).where(filled))
                or 0
            )
            return GroupCoverageRaw(
                sessions_covered=None,
                period_from=None,
                period_till=None,
                rows_total=int(total),
                rows_with_values=int(with_values),
            )

        column = getattr(model, session_column)
        scope = column.in_(sessions) if sessions else column.is_not(None)

        row = (
            await self._session.execute(
                select(
                    func.count(func.distinct(column)),
                    func.min(column),
                    func.max(column),
                    func.count(),
                ).where(scope)
            )
        ).one()

        with_values = (
            await self._session.scalar(select(func.count()).select_from(model).where(scope, filled))
            or 0
        )

        return GroupCoverageRaw(
            sessions_covered=int(row[0] or 0),
            period_from=row[1],
            period_till=row[2],
            rows_total=int(row[3] or 0),
            rows_with_values=int(with_values),
        )

    # --- поиск пропусков (spec 004) ----------------------------------------

    async def has_any_daily_bars(self) -> bool:
        """Есть ли в хранилище хоть одно наблюдение.

        Отличает дыру от отсутствия истории: на чистой базе календарь уже полон,
        и «пропущено 314 сессий» — нормальное состояние новой установки, а не
        авария. Догон там не нужен, нужна первичная загрузка.
        """
        row = await self._session.scalar(select(EquityDailyBar.session_date).limit(1))
        return row is not None

    async def sessions_with_daily_bars(self, sessions: list[dt.date]) -> set[dt.date]:
        """Сессии окна, за которые есть дневные котировки."""
        if not sessions:
            return set()
        rows = await self._session.scalars(
            select(EquityDailyBar.session_date)
            .where(EquityDailyBar.session_date.in_(sessions))
            .distinct()
        )
        return set(rows.all())

    async def sessions_with_successful_run(
        self, sessions: list[dt.date], source_id: str
    ) -> set[dt.date]:
        """Сессии окна, за которые источник отработал успешно.

        Успешный прогон при нуле наблюдений — законный исход: биржа ответила,
        данных за день нет. Такая сессия собрана, и повторять её незачем.
        """
        if not sessions:
            return set()

        # Исход закрывает СВОЙ ПЕРИОД, а не одну дату. Источник с выборкой за
        # диапазон записывает исход на конец периода: считая по дате, мы
        # объявляли бы пустыми все сессии диапазона, кроме последней, — данные
        # за них при этом лежат в таблице рядом (spec 008, FR-033).
        rows = await self._session.execute(
            select(IngestRun.session_date, IngestRun.period_from, IngestRun.period_till).where(
                IngestRun.source_id == source_id,
                IngestRun.status == "ok",
                or_(
                    IngestRun.session_date.in_(sessions),
                    and_(
                        IngestRun.period_from.is_not(None),
                        IngestRun.period_till.is_not(None),
                        IngestRun.period_from <= max(sessions),
                        IngestRun.period_till >= min(sessions),
                    ),
                ),
            )
        )

        covered: set[dt.date] = set()
        for session_date, period_from, period_till in rows.all():
            if period_from is not None and period_till is not None:
                covered |= {day for day in sessions if period_from <= day <= period_till}
            elif session_date is not None:
                covered.add(session_date)
        return covered

    async def attempts_by_session(self, sessions: list[dt.date]) -> dict[dt.date, int]:
        """Сколько раз сессию пытались собрать. Прогон, а не источник.

        Считаются различные `run_id`: один заход `ingest_session` пишет по
        строке на источник, и счёт по строкам дал бы десятку за одну попытку.

        Нужно пределу попыток: источник, недоступный за конкретную дату по своей
        природе, иначе перевыбирался бы вечно.
        """
        if not sessions:
            return {}
        rows = await self._session.execute(
            select(IngestRun.session_date, func.count(func.distinct(IngestRun.run_id)))
            .where(IngestRun.session_date.in_(sessions))
            .group_by(IngestRun.session_date)
        )
        return {day: count for day, count in rows.all() if day is not None}

    async def latest_ingest_at(self) -> dt.datetime | None:
        """Когда в хранилище в последний раз что-нибудь собирали.

        Отметка изменения данных целиком. По ней можно понять, мог ли вообще
        измениться любой набор: если с прошлого раза не собирали ничего, ответ
        о его устаревании остался прежним, и пересобирать набор незачем.
        """
        return await self._session.scalar(select(func.max(IngestRun.started_at)))

    async def last_attempt_by_session(
        self, sessions: list[dt.date], source_id: str
    ) -> dict[dt.date, dt.datetime]:
        """Когда по каждой сессии в последний раз ПЫТАЛИСЬ собрать источник.

        Успех попыткой тоже считается: вопрос здесь не «получилось ли», а «как
        давно ходили». Отметка нужна задержке перед повтором, и живёт она в
        хранилище, а не в памяти процесса, — иначе перезапуск снимал бы её и
        сбор начинал бы долбить биржу заново.
        """
        if not sessions:
            return {}
        rows = await self._session.execute(
            select(IngestRun.session_date, func.max(IngestRun.started_at))
            .where(
                IngestRun.session_date.in_(sessions),
                IngestRun.source_id == source_id,
            )
            .group_by(IngestRun.session_date)
        )
        return {day: at for day, at in rows.all() if day is not None and at is not None}

    async def failed_runs_for_sessions(self, sessions: list[dt.date]) -> list[IngestRun]:
        """Источники, оставшиеся незакрытыми за сессии окна.

        Берётся ПОСЛЕДНИЙ прогон каждой пары «сессия — источник», и только если
        он неуспешен. Иначе удачный повтор не снимал бы отметку: источник,
        упавший однажды и собранный со второй попытки, числился бы незакрытым
        вечно, а перечень пропусков превращался бы в журнал былых неудач.
        """
        if not sessions:
            return []
        rows = await self._session.scalars(
            select(IngestRun)
            .where(IngestRun.session_date.in_(sessions))
            .distinct(IngestRun.session_date, IngestRun.source_id)
            .order_by(
                IngestRun.session_date,
                IngestRun.source_id,
                IngestRun.started_at.desc(),
            )
        )
        return [run for run in rows.all() if run.status == "failed"]

    async def collected_since(
        self, moment: dt.datetime, exclude_sources: tuple[str, ...] = ()
    ) -> bool:
        """Собиралось ли хоть что-нибудь после указанного момента.

        Дешёвый ответ на дорогой вопрос. Содержимое набора — функция от
        сохранённых данных, а данные попадают в хранилище только через сбор, и
        каждый сбор здесь записан. Значит «после этого момента ничего не
        собирали» означает «вход измениться не мог» — и пересобирать набор ради
        дайджеста не нужно.

        Прогон, **не записавший ни строки**, сбором здесь не считается: он
        ничего не изменил. Иначе ежедневная сверка календаря, которая обычно не
        добавляет ни одной сессии, открывала бы дорогую ветку на весь день.

        Незавершённый прогон считается сбором: он может писать прямо сейчас.

        `exclude_sources` — источники, чьё влияние вызывающий проверяет иначе.
        Так исключается календарь: новые торговые дни появляются каждый день, но
        на окно ПРОШЕДШЕЙ даты влияют, только если попали внутрь него, а это
        видно по сдвигу границ окна.
        """
        conditions = [
            IngestRun.status == "ok",
            or_(
                IngestRun.finished_at.is_(None),
                and_(IngestRun.finished_at > moment, IngestRun.rows_written > 0),
            ),
        ]
        if exclude_sources:
            conditions.append(IngestRun.source_id.not_in(exclude_sources))

        found = await self._session.scalar(select(IngestRun.id).where(*conditions).limit(1))
        return found is not None

    # --- связи инструментов во времени (spec 008) --------------------------

    async def active_links_on(self, day: dt.date) -> dict[str, str]:
        """Действующие связи «бумага → семейство контрактов» на дату.

        Действующей считается связь, чей интервал накрывает дату. Интервалы
        одной бумаги не пересекаются, поэтому ответ однозначен.
        """
        rows = await self._session.execute(
            select(AssetFuturesLink.asset_id, AssetFuturesLink.contract_code).where(
                AssetFuturesLink.valid_from <= day,
                or_(AssetFuturesLink.valid_till.is_(None), AssetFuturesLink.valid_till >= day),
            )
        )
        return {row.asset_id: row.contract_code for row in rows.all()}

    async def link_history(self, asset_id: str) -> list[AssetFuturesLink]:
        """Все интервалы связи бумаги, от старых к новым."""
        rows = await self._session.scalars(
            select(AssetFuturesLink)
            .where(AssetFuturesLink.asset_id == asset_id)
            .order_by(AssetFuturesLink.valid_from)
        )
        return list(rows.all())

    async def open_link(
        self,
        asset_id: str,
        contract_code: str,
        valid_from: dt.date,
        chosen_by: str,
        open_interest: int | None = None,
    ) -> bool:
        """Открыть связь, закрыв прежнюю, если контракт сменился.

        Возвращает ``True``, если это СМЕНА контракта: такое изменение —
        событие для человека, а не тихая подмена. Продление действующей связи
        событием не является и возвращает ``False``.
        """
        current = await self._session.scalars(
            select(AssetFuturesLink)
            .where(AssetFuturesLink.asset_id == asset_id, AssetFuturesLink.valid_till.is_(None))
            .order_by(AssetFuturesLink.valid_from.desc())
            .limit(1)
        )
        active = current.first()

        if active is not None and active.contract_code == contract_code:
            return False

        if active is not None and valid_from <= active.valid_from:
            # Утверждение задним числом. Список серий описывает СЕГОДНЯШНИЙ
            # состав рынка, и сверка, датированная не позже уже подтверждённой
            # связи, историю не переписывает: иначе прежний интервал
            # закрывался бы датой раньше собственного начала, а такой интервал
            # не означает ничего (FR-049).
            logger.warning(
                "связь %s → %s не открыта: %s не позже начала действующей связи %s",
                asset_id,
                contract_code,
                valid_from,
                active.valid_from,
            )
            return False

        if active is not None:
            # Прежний интервал закрывается предыдущим днём: два действующих
            # интервала у одной бумаги означали бы, что мы не знаем, чем
            # спрашивать позиции.
            active.valid_till = valid_from - dt.timedelta(days=1)

        self._session.add(
            AssetFuturesLink(
                asset_id=asset_id,
                valid_from=valid_from,
                contract_code=contract_code,
                chosen_by=chosen_by,
                open_interest=open_interest,
            )
        )
        return active is not None

    async def assets_with_position_history(self) -> set[str]:
        """Бумаги, по которым позиции когда-либо собирались.

        Ими проверяется потеря соответствия: «фьючерса нет» и «мы его потеряли»
        различаются только наличием истории (FR-020a).
        """
        rows = await self._session.scalars(select(FuturesPosition.asset_id).distinct())
        return set(rows.all())

    async def earliest_links_after(self, day: dt.date) -> dict[str, str]:
        """Первые связи бумаг, начинающиеся ПОЗЖЕ этой даты.

        Нужно ручному сбору истории. Список серий отвечает про сегодня, поэтому
        связь подтверждается с той даты, когда её спросили, и никогда раньше:
        связи, заведённые ежедневным прогоном, начинаются сегодняшним днём.
        Без этого чтения догон прошлого остался бы без позиций навсегда
        (FR-017, исключение; FR-035).

        Бумаги, чья связь уже закрыта к этой дате, сюда не попадают: их
        инструмента не стало, и воскрешать его нельзя.

        Заглушка вместо семейства тоже не попадает: ею помечены бумаги, у
        которых контракта больше нет, и спросить биржу этим кодом значило бы
        выполнить обращение, заведомо не приносящее данных (FR-022).
        """
        first = (
            select(
                AssetFuturesLink.asset_id,
                func.min(AssetFuturesLink.valid_from).label("valid_from"),
            )
            .group_by(AssetFuturesLink.asset_id)
            .subquery()
        )
        rows = await self._session.execute(
            select(AssetFuturesLink.asset_id, AssetFuturesLink.contract_code)
            .join(
                first,
                and_(
                    AssetFuturesLink.asset_id == first.c.asset_id,
                    AssetFuturesLink.valid_from == first.c.valid_from,
                ),
            )
            .where(
                first.c.valid_from > day,
                AssetFuturesLink.contract_code != UNKNOWN_CONTRACT,
            )
        )
        return {row.asset_id: row.contract_code for row in rows.all()}

    async def record_closed_link(
        self,
        asset_id: str,
        valid_from: dt.date,
        valid_till: dt.date,
        contract_code: str,
        chosen_by: str,
    ) -> None:
        """Записать сразу закрытый интервал связи.

        Открыть и тут же закрыть двумя вызовами нельзя: закрытие идёт запросом
        UPDATE, а только что добавленный объект ещё не сброшен в базу, и запрос
        его не видит. Молча получался бы вечно действующий интервал — ровно то,
        чего эта таблица не должна допускать.
        """
        self._session.add(
            AssetFuturesLink(
                asset_id=asset_id,
                valid_from=valid_from,
                valid_till=valid_till,
                contract_code=contract_code,
                chosen_by=chosen_by,
            )
        )

    async def close_link(self, asset_id: str, valid_till: dt.date) -> bool:
        """Закрыть действующую связь датой.

        Возвращает ``True``, если было что закрывать. Закрытие — событие: без
        него исчезновение контракта выглядело бы пропуском сбора.
        """
        result = await self._session.execute(
            update(AssetFuturesLink)
            .where(
                AssetFuturesLink.asset_id == asset_id,
                AssetFuturesLink.valid_till.is_(None),
                # Интервал не закрывается раньше собственного начала: конец
                # раньше начала не означает ничего, и инвариант держится здесь,
                # а не на аккуратности вызывающего (FR-049).
                AssetFuturesLink.valid_from <= valid_till,
            )
            .values(valid_till=valid_till)
        )
        return bool(getattr(result, "rowcount", 0))

    async def commit(self) -> None:
        """Закрепить накопленное.

        Нужна отметке о начале обращения: пока она не закреплена, запись
        «источник пошёл за данными» живёт в памяти сессии и исчезает вместе с
        процессом — то есть в том единственном случае, ради которого её и
        заводят (FR-052). Все вызывающие закрепляют работу после каждого
        источника, поэтому незакреплённого здесь ничего не копится.
        """
        await self._session.commit()

    async def latest_link_start(self) -> dt.date | None:
        """Самая поздняя дата, которой связи уже подтверждены.

        По ней решается, есть ли источнику что сказать про окно прогона: список
        серий описывает СЕГОДНЯШНИЙ состав рынка, и догон, кончающийся раньше
        этой даты, связи пересматривать не должен (FR-049).
        """
        return await self._session.scalar(select(func.max(AssetFuturesLink.valid_from)))

    async def aliases_on(self, day: dt.date) -> dict[str, str]:
        """Имена бумаг, действующие на дату: «тикер → сущность».

        Переименованная бумага остаётся прежней сущностью, и наблюдения под
        новым именем обязаны лечь в тот же ряд (FR-038).
        """
        rows = await self._session.execute(
            select(AssetAlias.ticker, AssetAlias.asset_id).where(
                AssetAlias.valid_from <= day,
                or_(AssetAlias.valid_till.is_(None), AssetAlias.valid_till >= day),
            )
        )
        return {row.ticker: row.asset_id for row in rows.all()}

    async def asset_by_isin(self, isin: str) -> str | None:
        """Бумага с таким устойчивым идентификатором, если она уже известна.

        Так распознаётся переименование: тикер новый, сущность прежняя.
        """
        rows = await self._session.scalars(
            select(MarketAsset.asset_id).where(MarketAsset.isin == isin).limit(1)
        )
        return rows.first()

    async def upsert_alias(self, ticker: str, asset_id: str, valid_from: dt.date) -> None:
        """Записать имя бумаги на период.

        Действующий интервал того же имени той же сущности РАСШИРЯЕТСЯ назад, а
        не дополняется вторым интервалом. Прогон идёт от свежих сессий к старым
        и опознаёт бумаги на каждую, то есть каждый раз более ранней датой:
        десять сессий по две бумаги давали двадцать интервалов, все
        действующие одновременно, а на стенде это 506 бумаг на 314 сессий.
        Ровно этот рост уже чинила миграция 0012 (FR-048).

        Расширение и есть то, что мы узнали: имя указывает на сущность, и если
        оно действует с 18-го, то за 17-е оно указывает на неё же.
        """
        extended = await self._session.execute(
            update(AssetAlias)
            .where(
                AssetAlias.ticker == ticker,
                AssetAlias.asset_id == asset_id,
                AssetAlias.valid_till.is_(None),
                AssetAlias.valid_from > valid_from,
            )
            .values(valid_from=valid_from)
        )
        if getattr(extended, "rowcount", 0):
            return

        statement = (
            insert(AssetAlias)
            .values(ticker=ticker, asset_id=asset_id, valid_from=valid_from)
            .on_conflict_do_nothing(index_elements=["ticker", "valid_from"])
        )
        await self._session.execute(statement)

    async def close_alias(self, ticker: str, valid_till: dt.date) -> None:
        """Закрыть прежнее имя датой: дальше оно не действует."""
        await self._session.execute(
            update(AssetAlias)
            .where(AssetAlias.ticker == ticker, AssetAlias.valid_till.is_(None))
            .values(valid_till=valid_till)
        )

    # --- пропуски сессий с причинами (spec 008) ----------------------------

    async def record_skip(
        self,
        session_date: dt.date,
        reason: str,
        decided_at: dt.datetime,
        run_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Сохранить причину, по которой сессия не взята в работу."""
        statement = (
            insert(SessionSkip)
            .values(
                session_date=session_date,
                decided_at=decided_at,
                reason=reason,
                run_id=run_id,
                detail=detail,
            )
            .on_conflict_do_nothing(index_elements=["session_date", "decided_at"])
        )
        await self._session.execute(statement)

    # --- состав бумаг на дату (spec 008) -----------------------------------

    async def assets_traded_on(self, day: dt.date) -> set[str]:
        """Бумаги, у которых есть котировка за эту сессию.

        Знаменатель полноты. Не «все бумаги, когда-либо встречавшиеся в
        данных»: тот счёт только растёт и медленно врёт, потому что ушедшая с
        торгов бумага остаётся в нём навсегда.
        """
        rows = await self._session.scalars(
            select(EquityDailyBar.asset_id)
            .where(EquityDailyBar.session_date == day, EquityDailyBar.close.is_not(None))
            .distinct()
        )
        return set(rows.all())

    async def last_successful_run_at(self, source_id: str) -> dt.datetime | None:
        """Когда источник в последний раз отработал успешно — по любой дате.

        Нужно для источников, у которых спрашивать чаще раза в сутки нечего:
        торговый календарь меняется раз в день, и тик в минуту не должен
        превращаться в тысячу обращений к бирже. Признак берётся из хранилища
        исходов, а не из памяти процесса: перезапуск не должен его терять.
        """
        return await self._session.scalar(
            select(func.max(IngestRun.started_at)).where(
                IngestRun.source_id == source_id,
                IngestRun.status == "ok",
            )
        )

    async def runs_for_session(self, session_date: dt.date) -> list[IngestRun]:
        rows = await self._session.scalars(
            select(IngestRun)
            .where(IngestRun.session_date == session_date)
            .order_by(IngestRun.started_at)
        )
        return list(rows.all())


def _positions_filled() -> ColumnElement[bool]:
    """Условие «в строке позиций есть хоть одно значение»."""
    return or_(
        FuturesPosition.fiz_long.is_not(None),
        FuturesPosition.fiz_short.is_not(None),
        FuturesPosition.jur_long.is_not(None),
        FuturesPosition.jur_short.is_not(None),
    )
