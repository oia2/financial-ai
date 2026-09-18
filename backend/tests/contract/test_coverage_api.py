"""Состав бумаг в сводке — contracts/coverage-api.md (spec 008).

Неполнота группы позиций должна объясняться числами, а не догадкой: фьючерс
есть не у каждой бумаги, и её отсутствие — не пропуск. Числа считаются по
хранилищу: открытие раздела не должно стоить обращений к бирже.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from financial_ai.config import Settings
from financial_ai.market_data import coverage
from financial_ai.market_data.repository import DailyBar, MarketDataRepository

pytestmark = pytest.mark.db

ASOF = dt.date(2026, 9, 16)


async def seed(session: object, tickers: list[str]) -> MarketDataRepository:
    repository = MarketDataRepository(session)  # type: ignore[arg-type]
    await repository.add_trading_sessions([ASOF])

    bars = []
    for ticker in tickers:
        asset_id = f"EQ_AST_{ticker}"
        series_id = f"EQ_PRS_{ticker}"
        await repository.upsert_asset(asset_id, ticker, ASOF)
        await repository.upsert_price_series(series_id, asset_id, ASOF)
        bars.append(
            DailyBar(
                asset_id=asset_id,
                price_series_id=series_id,
                session_date=ASOF,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        )
    await repository.upsert_daily_bars(bars)
    return repository


async def test_знаменатель_это_бумаги_с_котировкой_за_дату(db_session: object) -> None:
    repository = await seed(db_session, ["SBER", "GAZP", "LKOH"])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]

    assert report["universe"] == {
        "assets": 3,
        "assets_with_futures": 0,
        "asof_date": ASOF.isoformat(),
    }
    assert repository is not None


async def test_бумаги_с_фьючерсом_считаются_по_действующей_связи(db_session: object) -> None:
    repository = await seed(db_session, ["SBER", "GAZP", "SGZH"])

    # Связь действует с более ранней даты и не закрыта — значит действует и на
    # дату сводки.
    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_F",
        valid_from=dt.date(2026, 9, 1),
        chosen_by="underlying_and_emitter",
    )
    await repository.open_link(
        asset_id="EQ_AST_GAZP",
        contract_code="GAZR_F",
        valid_from=dt.date(2026, 9, 1),
        chosen_by="underlying_and_emitter",
    )
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]

    # У SGZH фьючерса нет — и это не пропуск, а отсутствие инструмента.
    assert report["universe"] == {
        "assets": 3,
        "assets_with_futures": 2,
        "asof_date": ASOF.isoformat(),
    }


async def test_закрытая_связь_в_состав_не_попадает(db_session: object) -> None:
    repository = await seed(db_session, ["SBER"])
    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_F",
        valid_from=dt.date(2026, 8, 1),
        chosen_by="underlying_only",
    )
    # Смена контракта закрывает прежний интервал следующим днём после 2026-09-20,
    # то есть на дату сводки действует первый.
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    assert report["universe"]["assets_with_futures"] == 1

    await repository.open_link(
        asset_id="EQ_AST_SBER",
        contract_code="SBRF_MINI",
        valid_from=dt.date(2026, 9, 17),
        chosen_by="underlying_only",
    )
    await db_session.commit()  # type: ignore[attr-defined]

    # Прежняя связь закрыта 16.09, новая начинается 17.09: на 16.09 действует
    # ещё старая, и состав не меняется.
    again = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    assert again["universe"]["assets_with_futures"] == 1


async def test_у_группы_есть_исход_каждого_источника(db_session: object) -> None:
    await seed(db_session, ["SBER"])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    groups = {row["group"]: row for row in report["groups"]}

    # У «глобальных рядов» четыре источника: неполнота должна называть, какой
    # именно ряд не собрался, а не оставаться числом.
    assert len(groups["global"]["sources"]) == 4

    # У справочника оси сессий нет, но источники есть: пустой список читался бы
    # как «источников ноль».
    assert {source["source_id"] for source in groups["reference"]["sources"]} == {
        "equity_sectors",
        "equity_lot_sizes",
    }
    assert {source["source_id"] for source in groups["global"]["sources"]} == {
        "global_series",
        "cbr",
        "brent",
        "index_constituents",
    }


async def test_состав_считается_по_последней_собранной_сессии(db_session: object) -> None:
    """Несобранная сессия не выглядит отсутствием торгов (T054, FR-019a).

    Признак торгуемости выводится из наблюдений, поэтому «не торговалась» и «не
    собрали» по данным неразличимы. На несобранной дате состав вышел бы
    нулевым — и раздел сказал бы «бумаг нет», хотя их просто не собрали.
    """
    repository = await seed(db_session, ["SBER", "GAZP"])
    # Следующая сессия календарём известна, а котировок за неё нет.
    later = ASOF + dt.timedelta(days=1)
    await repository.add_trading_sessions([later])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), later)  # type: ignore[arg-type]

    assert report["universe"]["assets"] == 2
    # И дата, по которой состав посчитан, названа: она старше даты сводки.
    assert report["universe"]["asof_date"] == ASOF.isoformat()


async def test_следующий_сбор_это_несобранная_сессия_а_не_сегодня(db_session: object) -> None:
    """При отставании раздел не обещает сегодняшнюю дату (T055, FR-024a).

    Строка «следующий сбор» обязана называть сессию, которую сбор действительно
    возьмёт, иначе она говорит неправду ровно тогда, когда человек на неё и
    смотрит.
    """
    repository = await seed(db_session, ["SBER"])
    await repository.add_trading_sessions([ASOF + dt.timedelta(days=1)])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]

    # Котировки за ASOF есть, прочие источники не собраны — сессия недобрана,
    # и следующей будет именно она.
    assert report["next_session"] == ASOF.isoformat()


async def test_следующим_называется_самый_свежий_из_несобранных(db_session: object) -> None:
    """Порядок сбора и порядок обещания — один и тот же (FR-054).

    Ежедневный цикл идёт от свежих сессий к старым (FR-045), а строка
    продолжала называть самую раннюю — ту, до которой сбор дойдёт ПОСЛЕДНЕЙ.
    При отставании в восемьдесят дней она обещала прошлогоднюю дату там, где
    система собиралась взять вчерашнюю.
    """
    repository = await seed(db_session, ["SBER"])
    later = ASOF + dt.timedelta(days=1)
    await repository.add_trading_sessions([later])
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), later)  # type: ignore[arg-type]

    # Несобраны обе сессии; первой сбор возьмёт позднюю.
    assert report["next_session"] == later.isoformat()


async def test_ошибка_источника_названа_днём_и_причиной(db_session: object) -> None:
    """«Ошибка источника» без дня и причины — состояние, с которым нечего делать.

    Человеку нужно знать, что именно и когда сломалось: только так это можно
    проверить у источника и решить, ждать или вмешиваться.
    """
    repository = await seed(db_session, ["SBER"])
    await repository.record_run(
        run_id="run-brent",
        source_id="brent",
        status="failed",
        started_at=dt.datetime(2026, 9, 16, 19, 40, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 9, 16, 19, 41, tzinfo=dt.UTC),
        session_date=ASOF,
        rows_written=0,
        failure_reason="источник не ответил вовремя",
    )
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    groups = {row["group"]: row for row in report["groups"]}
    brent = next(s for s in groups["global"]["sources"] if s["source_id"] == "brent")

    assert brent["status"] == "failed"
    assert brent["failures"] == [
        {"session_date": ASOF.isoformat(), "reason": "источник не ответил вовремя"}
    ]

    # У собравшегося источника списка неудач нет — пустой, а не выдуманный.
    equity = next(s for s in groups["quotes"]["sources"] if s["source_id"] == "equity_d1")
    assert equity["failures"] == []


async def test_календарь_не_листается_в_пустоту(db_session: object) -> None:
    """Граница листания — по наблюдениям, а не по календарю (T091).

    Календарь знает торги с 2013 года, а собранного там нет и не
    предполагается: листать туда — листать пустые месяцы.
    """
    from financial_ai.market_data import calendar_view

    repository = await seed(db_session, ["SBER"])
    await repository.add_trading_sessions([dt.date(2013, 3, 25)])
    await db_session.commit()  # type: ignore[attr-defined]

    month = await calendar_view.build_month(db_session, Settings(), ASOF.year, ASOF.month)  # type: ignore[arg-type]

    assert month["earliest_month"] == f"{ASOF:%Y-%m}"


async def test_числа_строки_группы_и_её_источников_сходятся(db_session: object) -> None:
    """Одно число об одном и том же (T096, FR-032).

    Строка группы считалась по наблюдениям, а раскрытие — по исходам прогонов,
    и на экране рядом стояли «11 из 82» сверху и «13 из 82» внутри.
    """
    repository = await seed(db_session, ["SBER"])
    # Сессия без наблюдений, но с успешным прогоном: биржа ответила, данных за
    # день нет. Для правила полноты она закрыта.
    await repository.record_run(
        run_id="run-quotes",
        source_id="equity_d1",
        status="ok",
        started_at=dt.datetime(2026, 9, 16, 19, 40, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 9, 16, 19, 41, tzinfo=dt.UTC),
        session_date=ASOF,
        rows_written=0,
    )
    await db_session.commit()  # type: ignore[attr-defined]

    report = await coverage.build_report(db_session, Settings(), ASOF)  # type: ignore[arg-type]
    quotes = next(row for row in report["groups"] if row["group"] == "quotes")
    source = quotes["sources"][0]

    assert quotes["sessions_covered"] == source["sessions_covered"]
