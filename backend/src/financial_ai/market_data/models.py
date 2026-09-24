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
    JSON,
    CheckConstraint,
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

    # Минимальная партия торгов. NULL — «не получен», а не «единица»: актив без
    # лота в план портфеля не попадает, и это объясняется, а не подменяется
    # догадкой. Входом модели лот не является (spec 007).
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Устойчивый идентификатор сущности. Тикер именем бумаги быть перестаёт:
    # при переименовании он меняется, а ISIN — нет, и связь с фьючерсом от
    # переименования больше не рвётся (spec 008, FR-018). NULL — источник его
    # не отдал; тогда якорем остаётся тикер, как и раньше.
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)

    # Акция или пай фонда (FR-060). С 22.06.2026 фонды торгуются на той же
    # доске, что и акции, и различить их по наблюдениям нельзя. NULL — вид ещё
    # не получен от биржи; во вход модели идут только `share` (FR-060c).
    security_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)

    __table_args__ = (
        CheckConstraint("security_kind IN ('share', 'fund')", name="ck_market_asset_security_kind"),
    )


# Вид бумаги (FR-060).
KIND_SHARE = "share"
KIND_FUND = "fund"


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

    # Контракт, которым наблюдение собрано, — часть ключа. Без него повторный
    # сбор той же даты другим семейством контрактов молча затирал бы прежнее
    # наблюдение, и ряд склеивался бы из двух разных инструментов без следа
    # (spec 008, FR-030, FR-039).
    contract_code: Mapped[str] = mapped_column(String(32), primary_key=True)

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

    **Сборщика у этой таблицы больше нет.** Дивиденды исключены из ежедневного
    прогона: их никто не читал, а прогон платил за них обращением к брокеру по
    каждой бумаге (FR-008). Объявление таблицы остаётся, потому что то же
    правило требует сохранить уже собранные события: без объявления таблица
    осталась бы в базе никем не управляемой, и первая же сверка схемы решила
    бы, что она лишняя.
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


# Семейства контрактов у бумаги нет, а строка нужна: так помечается связь,
# которой не стало — и та, что была собрана до того, как связи начали хранить.
# Спрашивать биржу этим кодом нельзя: такого инструмента не существует.
UNKNOWN_CONTRACT = "unknown"


class AssetFuturesLink(Base):
    """Связь бумаги с семейством фьючерсных контрактов во времени.

    **Интервал, а не снимок.** Соответствие строилось из ISS на каждый прогон и
    жило в памяти: ответить «с какой даты у бумаги есть фьючерс» было нельзя, а
    смена семейства (классическое, мини, вечное — выбор идёт по открытому
    интересу) проходила бесследно и склеивала ряд позиций из двух инструментов.

    **Единица связи — семейство**, а не срочная серия: позиции запрашиваются
    именно семейством, а серия в запросе не участвует. Смена семейства —
    событие для человека, смена серии внутри него — нет (spec 008, FR-036).
    """

    __tablename__ = "market_asset_futures_link"
    __table_args__ = (Index("ix_asset_futures_link_asset", "asset_id"),)

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    valid_from: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    contract_code: Mapped[str] = mapped_column(String(32), nullable=False)

    # NULL — связь действует. Открытие новой закрывает прежнюю предыдущим днём.
    valid_till: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    # Чем связь подтверждена: `underlying_and_emitter` — базовый актив серии
    # совпал с бумагой И идентификатор эмитента у контракта совпал с эмитентом
    # бумаги; `underlying_only` — эмитент не проверен источником. Сверка
    # 2026-09-17: идентификатора базовой БУМАГИ биржа не отдаёт, эмитента —
    # отдаёт, но у обыкновенной и привилегированной он один (PROVENANCE.md).
    chosen_by: Mapped[str] = mapped_column(String(32), nullable=False)

    # Открытый интерес на момент выбора — основание, когда кандидатов несколько.
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)

    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssetAlias(Base):
    """Имя бумаги во времени.

    Отвечает на вопрос «этот тикер — новая бумага или прежняя под новым именем».
    Появление тикера с уже известным ISIN означает переименование: наблюдения
    относятся к прежней сущности, а прежнее имя закрывается датой.
    """

    __tablename__ = "market_asset_alias"
    __table_args__ = (Index("ix_asset_alias_asset", "asset_id"),)

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    valid_from: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_till: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class SessionSkip(Base):
    """Сессия, не взятая в работу, и причина.

    Причины принимались и раньше — и тут же терялись в логе. Ход прогона живёт
    в памяти процесса намеренно, а причина пропуска обязана жить дольше: без
    неё человек видит дыру и не знает, ждать ему или вмешиваться (FR-002).
    """

    __tablename__ = "market_session_skip"
    __table_args__ = (Index("ix_session_skip_session", "session_date"),)

    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    # withheld_until_close | retry_delay | attempts_exhausted | gap_over_limit.
    # Перечень закрытый: новая причина заводится вместе с местом, где решение
    # принимается, иначе на экране появится «пропущено» без объяснения.
    reason: Mapped[str] = mapped_column(String(32), nullable=False)

    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestRun(Base):
    """Исход сбора по одному источнику за одну сессию.

    Без этой таблицы вопрос «собралось ли всё» решался бы чтением логов.
    """

    __tablename__ = "market_ingest_run"
    __table_args__ = (
        # Сессия входит в ключ: прогон охватывает НЕСКОЛЬКО сессий, и без неё
        # догон был вынужден заводить идентификатор на каждый день — а журнал
        # группирует исходы по прогону и считает в нём сессии (FR-052).
        # ``postgresql_nulls_not_distinct`` обязателен: у календаря сессии нет,
        # и без него два его исхода в одном прогоне ключом не ограничивались бы.
        UniqueConstraint(
            "run_id",
            "source_id",
            "session_date",
            name="uq_ingest_run_source",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_ingest_run_session", "session_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # ok | failed | skipped. skipped — законный исход: неторговый день или
    # источник без данных на эту дату.
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    # daily | catchup | backfill. Прогон догона за вчерашнюю дату иначе
    # неотличим от обычного, а ручной сбор за прошлую дату — от догона.
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Почему работа не завершена: source | stopped | interrupted | internal.
    # Текст причины — для человека, перечень — для решения, что показать: без
    # него остановка и перезапуск показывались «ошибкой источника» (FR-033f).
    failure_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Период, который покрывает исход. У посессионного источника он совпадает с
    # сессией, у источника с выборкой за диапазон — это весь диапазон. Без
    # периода такой источник выглядит несобравшим всё, кроме последней сессии:
    # исход-то записывается на конец периода (spec 008, FR-033).
    period_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    period_till: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    # NULL — старый исход либо прогон, ещё не завершивший проверку полноты.
    coverage_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Причина завершения, включая законный успешный ответ без новых строк.
    coverage_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestSessionOutcome(Base):
    """Сохранённая свёртка точного плана одной сессии прогона."""

    __tablename__ = "market_ingest_session_outcome"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('collected', 'partial', 'failed', 'skipped', 'pending')",
            name="ck_ingest_session_outcome_value",
        ),
        Index("ix_ingest_session_outcome_run", "run_id"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    selected_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    interrupted: Mapped[bool] = mapped_column(nullable=False, default=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CoverageBoundary(Base):
    """Неизменяемая граница старой области, требующей аудита."""

    __tablename__ = "market_coverage_boundary"

    coverage_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    boundary_session: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceWorkEvidence(Base):
    """Проверенный исход одной объявленной единицы работы источника."""

    __tablename__ = "market_source_work_evidence"
    __table_args__ = (
        CheckConstraint(
            "result_kind IN ('value', 'confirmed_absence', 'not_applicable')",
            name="ck_source_work_evidence_result_kind",
        ),
        Index("ix_source_work_evidence_session", "session_date"),
    )

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    work_key: Mapped[str] = mapped_column(String(192), primary_key=True)
    coverage_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    result_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    origin_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RepairPlan(Base):
    """Явно созданный человеком план адресного восстановления."""

    __tablename__ = "market_repair_plan"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    # A plan is an audit artefact, not a moving view of today's rule.  Its
    # result must therefore remain reproducible after a coverage-rule change.
    coverage_version: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RepairItem(Base):
    """Одна пара «источник — дата» в плане ремонта."""

    __tablename__ = "market_repair_item"
    __table_args__ = (UniqueConstraint("plan_id", "session_date", "source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("market_repair_plan.plan_id"), nullable=False)
    session_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
