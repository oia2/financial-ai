"""Команды сбора рыночных данных.

Ручной запуск для сценариев проверки из quickstart.md. Боевой цикл запускается
планировщиком worker'а, а не отсюда.

    python -m financial_ai.market_data.cli run --session 2026-08-28
    python -m financial_ai.market_data.cli calendar --show-last 10
    python -m financial_ai.market_data.cli stats --session 2026-08-28
    python -m financial_ai.market_data.cli gaps
    python -m financial_ai.market_data.cli catchup --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session_factory
from financial_ai.logging import setup_logging
from financial_ai.market_data import backfill, gaps, ingest
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import cbr


def _parse_date(raw: str) -> dt.date:
    return dt.date.fromisoformat(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Сбор рыночных данных MOEX")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Собрать данные торговой сессии")
    run.add_argument(
        "--session",
        type=_parse_date,
        default=None,
        help="Дата сессии. По умолчанию — последняя завершённая по календарю.",
    )

    calendar = sub.add_parser("calendar", help="Показать торговые сессии календаря")
    calendar.add_argument("--show-last", type=int, default=10)

    stats = sub.add_parser("stats", help="Сколько наблюдений собрано за сессию")
    stats.add_argument("--session", type=_parse_date, required=True)

    verify = sub.add_parser(
        "verify-cbr",
        help="Сверить разбор страниц Банка России с живым ответом (нужен доступ к сети)",
    )
    verify.add_argument("--session", type=_parse_date, required=True)

    gaps_cmd = sub.add_parser("gaps", help="Какие сессии окна не собраны")
    gaps_cmd.add_argument(
        "--asof",
        type=_parse_date,
        default=None,
        help="Дата решения, от которой отсчитывается окно. По умолчанию — последняя сессия.",
    )

    catchup = sub.add_parser("catchup", help="Догнать пропущенные сессии окна")
    catchup.add_argument(
        "--asof",
        type=_parse_date,
        default=None,
        help="Дата решения, от которой отсчитывается окно. По умолчанию — последняя сессия.",
    )
    catchup.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать, что было бы собрано, и не обращаться к источникам.",
    )

    back = sub.add_parser("backfill", help="Первичная загрузка истории")
    back.add_argument(
        "--from",
        dest="date_from",
        default=None,
        help="Начальная дата. По умолчанию — из конфигурации; пусто — вся история.",
    )
    back.add_argument(
        "--ticker",
        action="append",
        default=None,
        help="Ограничить набор бумаг. Можно повторять. По умолчанию — все известные.",
    )

    return parser


async def _run(session_date: dt.date | None) -> int:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        result = await ingest.ingest_session(session, settings, session_date)

    print(f"прогон {result.run_id}, сессия {result.session_date}")
    for outcome in result.outcomes:
        line = f"  {outcome.source_id:<20} {outcome.status:<8} строк: {outcome.rows_written}"
        if outcome.failure_reason:
            line += f"  причина: {outcome.failure_reason}"
        print(line)

    if result.unfinished_sources:
        print(f"незакрытые источники: {', '.join(result.unfinished_sources)}")
        return 1
    return 0


async def _calendar(show_last: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        repository = MarketDataRepository(session)
        calendar = TradingCalendar(repository)
        latest = await calendar.latest_session()
        if latest is None:
            print("календарь пуст: выполните сбор")
            return 1
        window = await calendar.window(latest, show_last)
    print(f"последние {len(window)} торговых сессий:")
    for day in window:
        print(f"  {day}")
    return 0


async def _stats(session_date: dt.date) -> int:
    factory = get_session_factory()
    async with factory() as session:
        repository = MarketDataRepository(session)
        bars = await repository.count_daily_bars(session_date)
        runs = await repository.runs_for_session(session_date)
    print(f"сессия {session_date}: наблюдений по активам {bars}")
    for run in runs:
        print(f"  {run.source_id:<20} {run.status:<8} {run.trigger:<8} строк: {run.rows_written}")
    return 0


async def _resolve_asof(session: object, asof: dt.date | None) -> dt.date | None:
    if asof is not None:
        return asof
    repository = MarketDataRepository(session)  # type: ignore[arg-type]
    return await TradingCalendar(repository).latest_session()


async def _gaps(asof: dt.date | None) -> int:
    """Показать, каких сессий окна не хватает.

    Отдельного кода возврата для «дыра есть» нет: команда отвечает на вопрос,
    а не выносит вердикт.
    """
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        resolved = await _resolve_asof(session, asof)
        if resolved is None:
            print("календарь пуст: выполните сбор")
            return 1
        report = await gaps.find_gaps(session, settings, resolved)

    if report.needs_backfill:
        print("в хранилище нет наблюдений: нужна первичная загрузка (backfill)")
        return 0

    print(f"окно: {len(report.window)} торговых сессий, оканчивается {report.asof_date}")
    if not report.missing_sessions:
        print("пропущенных сессий нет")
    else:
        print(f"пропущено сессий: {len(report.missing_sessions)}")
        print()
        for day in report.missing_sessions:
            print(f"  {day}   нет котировок")

    if report.unfinished:
        print()
        print("незакрыто по источникам:")
        for item in report.unfinished:
            reason = item.reason or "причина не записана"
            print(f"  {item.session_date}   {item.source_id:<20} failed: {reason}")
    return 0


async def _catchup(asof: dt.date | None, dry_run: bool) -> int:
    """Закрыть дыру.

    Код 2 отделён от 1 намеренно: «догнали не всё» и «не смогли начать» —
    разные неисправности с разными последствиями.
    """
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        resolved = await _resolve_asof(session, asof)
        if resolved is None:
            print("календарь пуст: выполните сбор")
            return 1

        if dry_run:
            report = await gaps.find_gaps(session, settings, resolved)
            if report.needs_backfill:
                print("в хранилище нет наблюдений: нужна первичная загрузка (backfill)")
                return 1
            print(f"было бы собрано сессий: {len(report.missing_sessions)}")
            for day in report.missing_sessions:
                print(f"  {day}")
            return 0

        result = await ingest.catch_up(session, settings, resolved)

    if result.needs_backfill:
        print("в хранилище нет наблюдений: нужна первичная загрузка (backfill)")
        return 1
    if result.skipped_reason is not None:
        print(f"догон не выполнялся: {result.skipped_reason}")
        return 1
    if not result.attempted:
        print("пропущенных сессий нет")
        return 0

    print(
        f"догон от {result.requested[0]} до {result.requested[-1]}: сессий {len(result.requested)}"
    )
    for day in result.closed:
        print(f"  {day}  ok")
    for day in result.failed:
        print(f"  {day}  failed")
    print(f"итог: закрыто {len(result.closed)} из {len(result.requested)}")
    return 2 if result.failed else 0


async def _backfill(date_from: str | None, tickers: list[str] | None) -> int:
    settings = get_settings()
    if date_from is not None:
        settings = settings.model_copy(update={"market_data_backfill_from": date_from})

    config = ingest.build_iss_config(settings)
    factory = get_session_factory()

    async with IssClient(config) as iss, factory() as session:
        added = await backfill.backfill_calendar(session, settings, iss)
        print(f"календарь: добавлено сессий {added}")

        repository = MarketDataRepository(session)
        known = sorted(tickers or await repository.tickers_with_history())
        if not known:
            print(
                "список бумаг пуст: укажите --ticker либо выполните обычный сбор, "
                "чтобы система узнала состав доски"
            )
            return 1

        progress = await backfill.backfill_equity(session, settings, iss, known)

    print(f"загружено бумаг: {len(progress.completed)} из {progress.total}")
    return 0


async def _verify_cbr(session_date: dt.date) -> int:
    """Обратиться к ЦБ по-настоящему и показать разобранное.

    Тесты проверяют перенос разбора на образце ожидаемой структуры. Эта команда
    проверяет то, что образцом подтвердить нельзя: что настоящая страница ЦБ
    действительно имеет такую структуру.
    """
    config = cbr.CbrConfig()
    ok = True

    try:
        rates = await cbr.fetch_key_rate(config, session_date, session_date)
        print(f"ключевая ставка: разобрано значений {len(rates)}")
        for day, value in sorted(rates.items()):
            print(f"   {day}  {value}")
        if not rates:
            print("   ПУСТО — страница отвечает, но строк за эту дату нет")
    except cbr.CbrError as error:
        print(f"ключевая ставка: ОТКАЗ — {error}")
        ok = False

    try:
        zcyc = await cbr.fetch_zcyc(config, session_date, session_date)
        print(f"параметры ЗКЦ: рядов {len(zcyc)}")
        for series_id in sorted(zcyc):
            print(f"   {series_id}: {zcyc[series_id]}")
        if not zcyc:
            print("   ПУСТО — страница отвечает, но параметров за эту дату нет")
    except cbr.CbrError as error:
        print(f"параметры ЗКЦ: ОТКАЗ — {error}")
        ok = False

    if not ok:
        print()
        print("Разбор не сошёлся с живой страницей: вёрстка ЦБ могла измениться.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(get_settings().log_level)

    if args.command == "run":
        return asyncio.run(_run(args.session))
    if args.command == "calendar":
        return asyncio.run(_calendar(args.show_last))
    if args.command == "stats":
        return asyncio.run(_stats(args.session))
    if args.command == "verify-cbr":
        return asyncio.run(_verify_cbr(args.session))
    if args.command == "gaps":
        return asyncio.run(_gaps(args.asof))
    if args.command == "catchup":
        return asyncio.run(_catchup(args.asof, args.dry_run))
    if args.command == "backfill":
        return asyncio.run(_backfill(args.date_from, args.ticker))
    return 1


if __name__ == "__main__":
    sys.exit(main())
