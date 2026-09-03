"""Схема хранения рыночных данных по specs/003-moex-data-ingestion/data-model.md.

Цены — ``NUMERIC(28, 9)``, как и денежные величины в остальной схеме: ``float``
на пути «биржа → БД → набор» запрещён.

Пропуск хранится как ``NULL`` и никогда не заменяется нулём: отсутствие
наблюдения и нулевое значение — разные факты, и модель обязана их различать.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from financial_ai.db.models import Base

# Та же точность, что у денежных величин: nano плюс запас целой части.
PRICE = Numeric(28, 9)


class TradingSession(Base):
    """Торговая сессия на доске TQBR.

    Ось всего остального. Даты, которой здесь нет, для системы не существует:
    сбор за неё не выполняется, и «пропуском данных» её отсутствие не является.
    """

    __tablename__ = "market_trading_session"

    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketAsset(Base):
    """Экономический актив.

    ``ticker`` — маршрутный ключ для обращения к бирже, а не идентификатор:
    при переименовании он меняется, ``asset_id`` остаётся.
    """

    __tablename__ = "market_asset"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    first_seen_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_seen_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class PriceSeries(Base):
    """Сшиваемый ценовой ряд.

    Один ``asset_id`` может иметь несколько рядов: переименование сливает
    историю, разрыв — нет.
    """

    __tablename__ = "market_price_series"

    price_series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("market_asset.asset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    first_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class EquityDailyBar(Base):
    """Дневное наблюдение по активу за торговую сессию.

    Ключ — ``price_series_id + session_date``, а не актив: у актива с
    несколькими рядами наблюдения не должны сливаться в одну строку.
    """

    __tablename__ = "market_equity_daily_bar"
    __table_args__ = (
        Index("ix_equity_daily_bar_session", "session_date"),
        Index("ix_equity_daily_bar_asset_session", "asset_id", "session_date"),
    )

    price_series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # NULL означает «наблюдения нет». Нулём не заменяется и соседними
    # сессиями не достраивается.
    open: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    high: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    low: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    close: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Отпечаток значений: по нему видно переиздание биржей уже закрытой даты.
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GlobalDailySeries(Base):
    """Дневное значение ряда, не привязанного к активу.

    Индексы, курс USD, ключевая ставка, ЗКЦ, Brent.
    """

    __tablename__ = "market_global_daily_series"

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EquityAggregate(Base):
    """Дневные агрегаты торгов по активу: оборот, число сделок, средняя цена."""

    __tablename__ = "market_equity_aggregate"
    __table_args__ = (Index("ix_equity_aggregate_session", "session_date"),)

    price_series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)

    value: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    num_trades: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    waprice: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FuturesPosition(Base):
    """Позиции физических и юридических лиц по фьючерсам.

    Покрытие частичное по своей природе. ``NULL`` означает «не знаем», а не
    «позиций нет»: для модели это разные утверждения.
    """

    __tablename__ = "market_futures_position"
    __table_args__ = (Index("ix_futures_position_session", "session_date"),)

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    fiz_long: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    fiz_short: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    jur_long: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    jur_short: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssetSector(Base):
    """Отраслевая принадлежность эмитента.

    Справочник, а не дневной ряд: меняется редко и вне торговой сессии.
    """

    __tablename__ = "market_asset_sector"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DividendEvent(Base):
    """Дивидендное событие по активу.

    Ключ — актив плюс дата фиксации реестра: по ней событие однозначно.
    """

    __tablename__ = "market_dividend_event"
    __table_args__ = (Index("ix_dividend_event_asset", "asset_id"),)

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    declared_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_buy_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    payment_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    value: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)

    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestRun(Base):
    """Исход сбора по одному источнику за одну сессию.

    Без этой таблицы вопрос «собралось ли всё» решался бы чтением логов.
    """

    __tablename__ = "market_ingest_run"
    __table_args__ = (
        UniqueConstraint("run_id", "source_id", name="uq_ingest_run_source"),
        Index("ix_ingest_run_session", "session_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # ok | failed | skipped. skipped — законный исход: неторговый день или
    # источник без данных на эту дату.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
