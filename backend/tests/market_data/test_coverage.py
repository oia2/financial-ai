"""Тесты сводки состояния данных (US2).

Сводка отвечает на вопрос «что у нас есть» так, чтобы его не приходилось
задавать запросами к таблицам. Именно её отсутствие позволило трём дефектам из
четырёх прожить незамеченными.

**Два числа, а не одно.** Дефект позиций жил в зазоре между покрытием и долей
непустых строк: 224 сессии из 224 при 5 значениях на 57 029. Отчёт, считающий
только покрытие, объявил бы группу собранной — поэтому доля значений проверяется
отдельным тестом, воспроизводящим то самое состояние.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, coverage, groups
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.models import CoverageBoundary
from financial_ai.market_data.repository import DailyBar, MarketDataRepository, PositionRow
from financial_ai.market_data.verification import required_work_keys

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
    dt.date(2026, 9, 1),
]
ASOF = SESSIONS[-1]


@pytest.fixture
def settings() -> Settings:
    """Окна сужены до засеянных сессий: иначе в окно попадёт пустота."""
    return Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_global_window_sessions=len(SESSIONS),
        market_data_positions_window_sessions=len(SESSIONS),
    )


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("312.4"),
        high=Decimal("315.1"),
        low=Decimal("311.0"),
        close=Decimal("314.22"),
        volume=Decimal("1000"),
    )


async def _seed(
    session: AsyncSession,
    quotes: list[dt.date] | None = None,
    positions: list[PositionRow] | None = None,
    sectors: dict[str, str | None] | None = None,
) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    if quotes:
        await repository.upsert_daily_bars([_bar(day) for day in quotes])
        for day in quotes:
            await repository.record_run(
                run_id=f"seed-quotes-{day}",
                source_id="equity_d1",
                status="ok",
                started_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
                finished_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
                session_date=day,
                rows_written=1,
                coverage_version=2,
                coverage_reason="test_verified_work",
            )
            for work_key in required_work_keys("equity_d1"):
                await repository.record_work_evidence(
                    source_id="equity_d1",
                    session_date=day,
                    work_key=work_key,
                    result_kind="value",
                    reason_code="test_verified_work",
                    origin_run_id=None,
                )
    if positions:
        await repository.upsert_positions(positions)
    if sectors:
        await repository.upsert_sectors(sectors)
    await session.commit()
    return repository


def _group(report: dict[str, object], name: str) -> dict[str, object]:
    rows = report["groups"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["group"] == name)


# --- покрытие (FR-012, SC-004) ------------------------------------------------


async def test_coverage_matches_the_storage(db_session: AsyncSession, settings: Settings) -> None:
    """Покрытие, доля окна, границы периода и число дыр совпадают с данными."""
    await _seed(db_session, quotes=[SESSIONS[0], SESSIONS[1], SESSIONS[4]])

    report = await coverage.build_report(db_session, settings, ASOF)
    quotes = _group(report, "quotes")

    assert quotes["window_sessions"] == len(SESSIONS)
    assert quotes["sessions_covered"] == 3
    assert quotes["coverage_ratio"] == pytest.approx(0.6)
    assert quotes["period_from"] == SESSIONS[0].isoformat()
    assert quotes["period_till"] == SESSIONS[4].isoformat()
    assert quotes["gaps"] == 2


async def test_full_window_has_no_gaps(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session, quotes=SESSIONS)

    report = await coverage.build_report(db_session, settings, ASOF)
    quotes = _group(report, "quotes")

    assert quotes["coverage_ratio"] == 1.0
    assert quotes["gaps"] == 0


async def test_legacy_coverage_requires_audit_without_automatic_backfill(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Boundary captures old empty/successful dates and is not an auto-catchup job."""
    repository = MarketDataRepository(db_session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    await repository.upsert_daily_bars([_bar(day) for day in SESSIONS[:2]])
    db_session.add(CoverageBoundary(coverage_version=2, boundary_session=SESSIONS[2]))
    # A legacy `ok` must not become proof merely because it has zero failures.
    await repository.record_run(
        run_id="legacy-ok",
        source_id="equity_d1",
        status="ok",
        started_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
        session_date=SESSIONS[0],
        rows_written=1,
        coverage_version=2,
        coverage_reason="test_verified_work",
    )
    # У старого успеха доказательств единиц работы нет: таблица доказательств
    # появилась вместе с версией 2, и закрывают именно они (FR-032f).
    await db_session.flush()
    from financial_ai.market_data.models import IngestRun

    await db_session.execute(
        update(IngestRun).where(IngestRun.run_id == "legacy-ok").values(coverage_version=None)
    )
    await db_session.commit()

    report = await coverage.build_report(db_session, settings, ASOF)
    quotes = _group(report, "quotes")
    assert quotes["requires_audit"] >= 3
    assert quotes["sessions_covered"] == 0

    automatic = await completeness.incomplete_sessions(
        repository,
        TradingCalendar(repository),
        settings,
        ASOF,
        closed=SESSIONS,
    )
    assert not set(automatic) & set(SESSIONS[:3])
    assert set(automatic) & set(SESSIONS[3:])

    # An explicit successful repair can validate its own date without moving
    # the one-time boundary; subsequent fresh failures still stay incomplete.
    await repository.record_run(
        run_id="repair-one-date",
        source_id="equity_d1",
        status="ok",
        started_at=dt.datetime(2026, 9, 4, 19, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 9, 4, 19, tzinfo=dt.UTC),
        session_date=SESSIONS[0],
        rows_written=1,
        coverage_version=2,
        coverage_reason="test_verified_work",
    )
    await repository.record_work_evidence(
        source_id="equity_d1",
        session_date=SESSIONS[0],
        work_key="board:TQBR",
        result_kind="value",
        reason_code="test_verified_work",
        origin_run_id="repair-one-date",
    )
    await db_session.commit()
    closed = await completeness.closed_sessions(
        repository, groups.BY_ID[groups.GroupId.QUOTES], "equity_d1", SESSIONS
    )
    assert SESSIONS[0] in closed
    assert await repository.coverage_boundary() == SESSIONS[2]


async def test_group_without_a_single_row_shows_zeroes_not_absence(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Пустая группа отличима от отсутствующей: она в отчёте есть."""
    await _seed(db_session, quotes=SESSIONS)

    report = await coverage.build_report(db_session, settings, ASOF)
    aggregates = _group(report, "aggregates")

    assert aggregates["rows_total"] == 0
    assert aggregates["coverage_ratio"] == 0.0
    assert aggregates["value_ratio"] is None


# --- доля непустых (FR-013, SC-005) ------------------------------------------


async def test_collected_but_empty_is_distinguishable_from_collected(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Воспроизводит состояние позиций на 2026-09-03.

    Покрытие полное, значений нет. Отчёт, показывающий только первое, объявил
    бы группу собранной — именно так дефект и прожил незамеченным.
    """
    empty_rows = [
        PositionRow(
            asset_id="EQ_AST_SBER",
            session_date=day,
            fiz_long=None,
            fiz_short=None,
            jur_long=None,
            jur_short=None,
        )
        for day in SESSIONS
    ]
    await _seed(db_session, quotes=SESSIONS, positions=empty_rows)

    # Источник отработал успешно и записал пустые строки — именно так дефект и
    # выглядел. Сессию закрывает успешный прогон: пустая строка сама по себе
    # покрытием не считается, иначе дыра пряталась бы дважды.
    repository = MarketDataRepository(db_session)
    for day in SESSIONS:
        await repository.record_run(
            run_id=f"seed-positions-{day}",
            source_id="futures_positions",
            status="ok",
            started_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
            finished_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
            session_date=day,
            rows_written=1,
            coverage_version=2,
            coverage_reason="test_verified_work",
        )
        await repository.record_work_evidence(
            source_id="futures_positions",
            session_date=day,
            work_key="applicable_links",
            result_kind="confirmed_absence",
            reason_code="test_verified_empty",
            origin_run_id=f"seed-positions-{day}",
        )
    await db_session.commit()

    report = await coverage.build_report(db_session, settings, ASOF)
    positions = _group(report, "positions")

    assert positions["coverage_ratio"] == 1.0
    assert positions["value_ratio"] == 0.0
    assert positions["rows_total"] == len(SESSIONS)
    assert positions["rows_with_values"] == 0
    # Вывод делает сам ответ: иначе порог повторился бы в команде и в
    # интерфейсе и при первом уточнении разошёлся (FR-013a).
    assert positions["looks_collected_but_empty"] is True
    assert _group(report, "quotes")["looks_collected_but_empty"] is False


async def test_report_carries_the_catchup_window(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Окно догона приходит из ответа, а не выводится из строк сводки.

    Окна групп различаются, а форме запуска нужно одно — то, по которому
    планируется прогон (FR-013b).
    """
    await _seed(db_session, quotes=SESSIONS)

    report = await coverage.build_report(db_session, settings, ASOF)
    window = report["catchup_window"]
    assert isinstance(window, dict)

    assert window["sessions"] == len(SESSIONS)
    assert window["date_from"] == SESSIONS[0].isoformat()
    assert window["date_till"] == ASOF.isoformat()


async def test_partial_values_are_not_read_as_empty(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Настоящая частичность — не пустота: одна сторона из четырёх считается."""
    rows = [
        PositionRow(
            asset_id="EQ_AST_SBER",
            session_date=SESSIONS[0],
            fiz_long=None,
            fiz_short=None,
            jur_long=Decimal("1"),
            jur_short=None,
        ),
        PositionRow(
            asset_id="EQ_AST_SBER",
            session_date=SESSIONS[1],
            fiz_long=None,
            fiz_short=None,
            jur_long=None,
            jur_short=None,
        ),
    ]
    await _seed(db_session, quotes=SESSIONS, positions=rows)

    report = await coverage.build_report(db_session, settings, ASOF)
    positions = _group(report, "positions")

    assert positions["rows_with_values"] == 1
    assert positions["value_ratio"] == 0.5


# --- справочники (FR-014) -----------------------------------------------------


async def test_reference_group_has_no_window_fields(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Группа без оси сессий не бывает недобранной: полей окна у неё нет."""
    await _seed(db_session, quotes=SESSIONS, sectors={"EQ_AST_SBER": "Финансы"})

    report = await coverage.build_report(db_session, settings, ASOF)
    reference = _group(report, "reference")

    assert reference["has_history"] is False
    for absent in ("window_sessions", "coverage_ratio", "sessions_covered", "gaps"):
        assert absent not in reference


async def test_reference_group_still_reports_values(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Наполненность у справочника есть, даже когда истории нет."""
    await _seed(db_session, quotes=SESSIONS, sectors={"EQ_AST_SBER": "Финансы"})

    report = await coverage.build_report(db_session, settings, ASOF)
    reference = _group(report, "reference")

    assert reference["rows_total"] == 1
    assert reference["value_ratio"] == 1.0


# --- отчёт о полноте, а не просмотр данных (FR-015) --------------------------


async def test_report_carries_no_observation_values(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Ни цен, ни объёмов, ни позиций в ответе нет."""
    await _seed(
        db_session,
        quotes=SESSIONS,
        positions=[
            PositionRow(
                asset_id="EQ_AST_SBER",
                session_date=SESSIONS[0],
                fiz_long=Decimal("125484"),
                fiz_short=None,
                jur_long=None,
                jur_short=None,
            )
        ],
    )

    report = await coverage.build_report(db_session, settings, ASOF)
    rendered = repr(report)

    assert "314.22" not in rendered
    assert "125484" not in rendered
    assert "SBER" not in rendered


async def test_all_five_groups_are_reported(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session, quotes=SESSIONS)

    report = await coverage.build_report(db_session, settings, ASOF)
    rows = report["groups"]
    assert isinstance(rows, list)

    assert [row["group"] for row in rows] == [
        "quotes",
        "aggregates",
        "global",
        "positions",
        "reference",
    ]


async def test_report_is_computed_not_stored(db_session: AsyncSession, settings: Settings) -> None:
    """Сводка пересчитывается по данным и разойтись с ними не может."""
    repository = await _seed(db_session, quotes=[SESSIONS[0]])
    before = _group(await coverage.build_report(db_session, settings, ASOF), "quotes")

    await repository.upsert_daily_bars([_bar(SESSIONS[1])])
    await repository.record_run(
        run_id="computed-report-second-session",
        source_id="equity_d1",
        status="ok",
        started_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 9, 3, 19, tzinfo=dt.UTC),
        session_date=SESSIONS[1],
        rows_written=1,
        coverage_version=2,
        coverage_reason="test_verified_work",
    )
    await repository.record_work_evidence(
        source_id="equity_d1",
        session_date=SESSIONS[1],
        work_key="board:TQBR",
        result_kind="value",
        reason_code="test_verified_work",
        origin_run_id="computed-report-second-session",
    )
    await db_session.commit()
    after = _group(await coverage.build_report(db_session, settings, ASOF), "quotes")

    assert before["sessions_covered"] == 1
    assert after["sessions_covered"] == 2
