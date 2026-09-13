"""Схема хранения прогонов Daily ML по specs/007-daily-ml-lifecycle/data-model.md.

Прогон хранится, а состояние догона — нет, и это не непоследовательность.
Догон — процесс: его состояние осмысленно, только пока он идёт, и хранимое
«идёт» после падения превращается в вечную блокировку. Прогон — решение: он
остаётся фактом и после перезапуска, и через год.

Отсюда правило, которого у догона нет: ``running``, обнаруженный при старте,
означает обрыв и переводится в ``failed``. Обработчик один, держать запись
больше некому.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
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

# Скор участвует в сортировке: float сделал бы порядок зависимым от
# представления. Та же точность, что у денежных величин проекта.
SCORE = Numeric(28, 9)


class RunStatus(StrEnum):
    """Состояние прогона.

    Ровно четыре, и каждое описывает сам прогон. Пауза — настройка режима, а не
    состояние даты; устаревание входа — отношение прогона к текущим данным и
    вычисляется; «уже посчитано» — отсутствие работы, а не её исход.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class DailyMlRun(Base):
    """Одно выполнение ранжирования за дату решения."""

    __tablename__ = "daily_ml_run"
    __table_args__ = (
        # Ключ идемпотентности. Проверка «посмотрим, потом вставим» между двумя
        # процессами не атомарна; уникальный индекс атомарен по устройству.
        UniqueConstraint(
            "asof_date",
            "dataset_digest",
            "model_id",
            "model_version",
            name="uq_daily_ml_run_input",
        ),
        Index("ix_daily_ml_run_asof", "asof_date"),
        Index("ix_daily_ml_run_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    asof_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    dataset_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    # Набор удаляется ретеншеном; ссылка остаётся, чтобы было видно, что именно
    # уходило модели, даже когда файлов уже нет.
    dataset_ref: Mapped[str] = mapped_column(Text, nullable=False)

    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Причина, пригодная для показа. Текст исключения сюда не попадает: он несёт
    # адрес обращения, и адрес может быть внутренним.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    included_asset_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Свойства входа и исхода, которые нельзя пересчитать задним числом.
    # Глубина окна — настройка, полнота входа меняется с приходом данных, а
    # звено ранжирования однажды перестанет быть эмулятором.
    window_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    window_till: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    input_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    emulated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class DailyMlRankingItem(Base):
    """Одна позиция сохранённого ранжирования.

    Хранится вместе с прогоном, потому что набор удаляется через 30 дней, а
    решение должно оставаться читаемым и после этого.
    """

    __tablename__ = "daily_ml_ranking_item"
    __table_args__ = (Index("ix_daily_ml_ranking_item_asset", "run_id", "asset_id"),)

    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("daily_ml_run.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)

    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    price_series_id: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
