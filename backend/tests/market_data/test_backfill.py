"""Тесты первичной загрузки истории.

Главное свойство — возобновляемость: прерванная на третьем часу загрузка не
должна означать три потерянных часа.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import backfill
from financial_ai.market_data.iss.client import IssError
from financial_ai.market_data.repository import MarketDataRepository

pytestmark = pytest.mark.db

DAYS = [dt.date(2026, 8, 26), dt.date(2026, 8, 27), dt.date(2026, 8, 28)]


class FakeIss:
    """Подделка биржи. Считает обращения по бумагам — это проверяемое поведение."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.security_calls: list[str] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.security_calls.append(secid)
        if secid in self.fail_for:
            raise IssError("биржа недоступна")
        return [
            {
                "SECID": secid,
                "TRADEDATE": day.isoformat(),
                "OPEN": "312.4",
                "HIGH": "315.1",
                "LOW": "311.0",
                "CLOSE": "314.22",
                "VOLUME": "1000",
            }
            for day in DAYS
        ]


@pytest.fixture
def settings() -> Settings:
    return Settings()


# --- глубина -----------------------------------------------------------------


def test_empty_setting_means_all_available_history() -> None:
    """Пустая настройка — вся история: докачка потом обойдётся дороже."""
    assert (
        backfill.resolve_start_date(Settings(market_data_backfill_from=""))
        == backfill.EARLIEST_DATE
    )


def test_configured_date_is_used() -> None:
    settings = Settings(market_data_backfill_from="2025-01-01")
    assert backfill.resolve_start_date(settings) == dt.date(2025, 1, 1)


def test_malformed_date_falls_back_to_full_history() -> None:
    """Опечатка не должна тихо обрезать историю до нуля."""
    settings = Settings(market_data_backfill_from="первое января")
    assert backfill.resolve_start_date(settings) == backfill.EARLIEST_DATE


# --- загрузка ----------------------------------------------------------------


async def test_history_is_loaded(db_session: AsyncSession, settings: Settings) -> None:
    iss = FakeIss()
    await backfill.backfill_equity(db_session, settings, iss, ["SBER", "GAZP"], till=DAYS[-1])

    repository = MarketDataRepository(db_session)
    assert await repository.count_daily_bars(DAYS[-1]) == 2
    bars = await repository.daily_bars_for_window(DAYS)
    assert len(bars) == 6
    assert bars[0].close == Decimal("314.22")


async def test_backfill_walks_securities_not_dates(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Первичная загрузка ходит по бумагам — за всю историю сразу."""
    iss = FakeIss()
    await backfill.backfill_equity(db_session, settings, iss, ["SBER", "GAZP"], till=DAYS[-1])
    assert iss.security_calls == ["SBER", "GAZP"]


# --- возобновляемость --------------------------------------------------------


async def test_completed_tickers_are_skipped(db_session: AsyncSession, settings: Settings) -> None:
    """FR-009: повторный запуск продолжает, а не начинает заново."""
    first = FakeIss()
    await backfill.backfill_equity(db_session, settings, first, ["SBER", "GAZP"], till=DAYS[-1])

    second = FakeIss()
    await backfill.backfill_equity(
        db_session, settings, second, ["SBER", "GAZP", "LKOH"], till=DAYS[-1]
    )

    assert second.security_calls == ["LKOH"]


async def test_progress_reports_remaining(db_session: AsyncSession, settings: Settings) -> None:
    await backfill.backfill_equity(db_session, settings, FakeIss(), ["SBER"], till=DAYS[-1])
    progress = await backfill.backfill_equity(
        db_session, settings, FakeIss(), ["SBER", "GAZP", "LKOH"], till=DAYS[-1]
    )
    assert progress.total == 3
    assert "SBER" in progress.completed


async def test_interruption_keeps_loaded_data(db_session: AsyncSession, settings: Settings) -> None:
    """Одна недоступная бумага не отменяет уже загруженные."""
    iss = FakeIss(fail_for={"GAZP"})
    await backfill.backfill_equity(
        db_session, settings, iss, ["SBER", "GAZP", "LKOH"], till=DAYS[-1]
    )

    repository = MarketDataRepository(db_session)
    tickers = await repository.tickers_with_history()
    assert tickers == {"SBER", "LKOH"}


async def test_failed_ticker_is_retried_next_run(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Не загрузившаяся бумага должна попасть в следующий прогон."""
    await backfill.backfill_equity(
        db_session, settings, FakeIss(fail_for={"GAZP"}), ["SBER", "GAZP"], till=DAYS[-1]
    )
    second = FakeIss()
    await backfill.backfill_equity(db_session, settings, second, ["SBER", "GAZP"], till=DAYS[-1])
    assert second.security_calls == ["GAZP"]


async def test_calendar_is_filled_first(db_session: AsyncSession, settings: Settings) -> None:
    """Пока неизвестно, какие дни были торговыми, остальное не имеет смысла."""
    added = await backfill.backfill_calendar(db_session, settings, FakeIss())
    assert added == len(DAYS)

    repository = MarketDataRepository(db_session)
    assert await repository.is_trading_session(DAYS[0]) is True


# --- один прогон на всю загрузку (FR-052) ------------------------------------


async def test_загрузка_это_один_прогон_а_не_прогон_на_бумагу(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Журнал группирует исходы по прогону, и бумага прогоном не является.

    Пятьсот шесть бумаг давали пятьсот шесть прогонов по одному источнику, и
    список последних прогонов вмещал пять бумаг вместо одной загрузки
    (FR-052).
    """
    from sqlalchemy import func, select

    from financial_ai.market_data.models import IngestRun

    await backfill.backfill_equity(db_session, settings, FakeIss(), ["SBER", "GAZP"], till=DAYS[-1])
    await db_session.commit()

    runs = await db_session.scalar(
        select(func.count(func.distinct(IngestRun.run_id))).where(IngestRun.trigger == "backfill")
    )
    assert runs == 1


async def test_исход_загрузки_копит_число_наблюдений(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Обратная форма: один прогон не должен стоить видимости объёма работы."""
    from sqlalchemy import select

    from financial_ai.market_data.models import IngestRun

    await backfill.backfill_equity(db_session, settings, FakeIss(), ["SBER", "GAZP"], till=DAYS[-1])
    await db_session.commit()

    written = await db_session.scalar(
        select(IngestRun.rows_written).where(IngestRun.trigger == "backfill")
    )
    assert written == 6


async def test_загрузка_переименованной_бумаги_продолжает_ряд(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Иначе загрузка заводит вторую бумагу, и история начинается с нуля (FR-048)."""
    repository = MarketDataRepository(db_session)
    await repository.upsert_asset("EQ_AST_MULTOLD", "MULTOLD", DAYS[-1])
    await repository.upsert_alias("MULTNEW", "EQ_AST_MULTOLD", DAYS[0])
    await db_session.commit()

    await backfill.backfill_equity(db_session, settings, FakeIss(), ["MULTNEW"], till=DAYS[-1])

    bars = await repository.daily_bars_for_window(DAYS)
    assert {bar.asset_id for bar in bars} == {"EQ_AST_MULTOLD"}


# --- охваченный период (ревью 2026-09-24, R4) ---------------------------------


class RangeIss(FakeIss):
    """Запоминает запрошенные диапазоны, а не только бумаги."""

    def __init__(self) -> None:
        super().__init__()
        self.ranges: list[tuple[str, str, str]] = []

    async def fetch_security_history(
        self, secid: str, date_from: str, date_till: str, columns: tuple[str, ...]
    ) -> list[dict[str, object]]:
        self.ranges.append((secid, date_from, date_till))
        return await super().fetch_security_history(secid, date_from, date_till, columns)


def _from(day: dt.date) -> Settings:
    return Settings(market_data_backfill_from=day.isoformat())


async def test_exact_repeat_does_no_work(db_session: AsyncSession) -> None:
    await backfill.backfill_equity(db_session, _from(DAYS[1]), RangeIss(), ["SBER"], till=DAYS[-1])

    again = RangeIss()
    progress = await backfill.backfill_equity(
        db_session, _from(DAYS[1]), again, ["SBER"], till=DAYS[-1]
    )

    assert again.ranges == []
    assert progress.remaining == 0


async def test_earlier_start_loads_only_the_uncovered_head(db_session: AsyncSession) -> None:
    """Прежде доказательство ``ticker:SBER`` закрывало любой диапазон, и более
    ранний ``--from`` не делал ни одного запроса."""
    repository = MarketDataRepository(db_session)
    await backfill.backfill_equity(db_session, _from(DAYS[1]), RangeIss(), ["SBER"], till=DAYS[-1])
    assert {bar.session_date for bar in await repository.daily_bars_for_window(DAYS)} == set(
        DAYS[1:]
    )

    earlier = RangeIss()
    progress = await backfill.backfill_equity(
        db_session, _from(DAYS[0]), earlier, ["SBER"], till=DAYS[-1]
    )

    assert earlier.ranges == [("SBER", DAYS[0].isoformat(), DAYS[0].isoformat())]
    assert progress.remaining == 0
    assert {bar.session_date for bar in await repository.daily_bars_for_window(DAYS)} == set(DAYS)


async def test_later_end_loads_only_the_uncovered_tail(db_session: AsyncSession) -> None:
    await backfill.backfill_equity(db_session, _from(DAYS[0]), RangeIss(), ["SBER"], till=DAYS[1])

    later = RangeIss()
    await backfill.backfill_equity(db_session, _from(DAYS[0]), later, ["SBER"], till=DAYS[-1])

    assert later.ranges == [("SBER", DAYS[-1].isoformat(), DAYS[-1].isoformat())]


async def test_legacy_evidence_without_start_proves_no_range(db_session: AsyncSession) -> None:
    """Доказательство прежнего формата начала не хранит и покрытием не считается."""
    repository = MarketDataRepository(db_session)
    await repository.record_work_evidence(
        source_id=backfill.HISTORY_SOURCE_ID,
        session_date=DAYS[-1],
        work_key="ticker:SBER",
        result_kind="value",
        reason_code="history_loaded",
        origin_run_id=None,
    )
    await db_session.commit()

    iss = RangeIss()
    await backfill.backfill_equity(db_session, _from(DAYS[0]), iss, ["SBER"], till=DAYS[-1])

    assert iss.ranges == [("SBER", DAYS[0].isoformat(), DAYS[-1].isoformat())]


def test_uncovered_subtracts_proved_spans() -> None:
    d = dt.date
    spans = [(d(2025, 1, 1), d(2025, 6, 30)), (d(2025, 9, 1), d(2025, 12, 31))]

    assert backfill.uncovered(spans, d(2025, 1, 1), d(2025, 12, 31)) == [
        (d(2025, 7, 1), d(2025, 8, 31))
    ]
    assert backfill.uncovered(spans, d(2024, 1, 1), d(2026, 1, 31)) == [
        (d(2024, 1, 1), d(2024, 12, 31)),
        (d(2025, 7, 1), d(2025, 8, 31)),
        (d(2026, 1, 1), d(2026, 1, 31)),
    ]
    assert backfill.uncovered(spans, d(2025, 2, 1), d(2025, 3, 1)) == []
