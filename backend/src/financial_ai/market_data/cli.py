"""Команды сбора рыночных данных.

Ручной запуск для сценариев проверки из quickstart.md. Боевой цикл запускается
планировщиком worker'а, а не отсюда.

    python -m financial_ai.market_data.cli run --session 2026-08-28
    python -m financial_ai.market_data.cli calendar --show-last 10
    python -m financial_ai.market_data.cli stats --session 2026-08-28
    python -m financial_ai.market_data.cli gaps
    python -m financial_ai.market_data.cli coverage
    python -m financial_ai.market_data.cli catchup --group quotes --from 2026-06-01
    python -m financial_ai.market_data.cli catchup-status
    python -m financial_ai.market_data.cli catchup-stop

Догон выполняется НЕ здесь: команда обращается к внутреннему интерфейсу
worker'а, в котором он и живёт фоновой задачей.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

import httpx

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session_factory
from financial_ai.logging import setup_logging
from financial_ai.market_data import backfill, gaps, ingest, links
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import brent, cbr, reference
from financial_ai.market_data.sources.positions_client import PositionsClient


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

    # Живые сверки. В `scripts/check.sh` они НЕ входят: сеть в среде сборки
    # недоступна, и гейт стал бы недетерминированным (FR-026).
    verify_brent = sub.add_parser(
        "verify-brent",
        help="Обратиться к бирже за Brent по-настоящему и показать разобранное",
    )
    verify_brent.add_argument("--session", type=_parse_date, required=True)

    verify_positions = sub.add_parser(
        "verify-positions",
        help="Обратиться к источнику позиций по-настоящему и показать разобранное",
    )
    verify_positions.add_argument("--session", type=_parse_date, required=True)
    verify_positions.add_argument(
        "--ticker",
        default="SBER",
        help="Тикер акции, по фьючерсу которой спрашиваются позиции.",
    )

    verify_reference = sub.add_parser(
        "verify-reference",
        help="Проверить на живом ответе веса в индексе и отрасли эмитентов",
    )
    verify_reference.add_argument("--session", type=_parse_date, required=True)

    gaps_cmd = sub.add_parser("gaps", help="Какие сессии окна не собраны")
    gaps_cmd.add_argument(
        "--asof",
        type=_parse_date,
        default=None,
        help="Дата решения, от которой отсчитывается окно. По умолчанию — последняя сессия.",
    )

    # Окно догона отсчитывает worker: он и знает, что уже собрано. Отдельного
    # `--asof` здесь нет — он позволял бы задать одну дату решения команде и
    # другую тому, кто на самом деле собирает.
    catchup = sub.add_parser("catchup", help="Догнать пропущенные сессии окна")
    catchup.add_argument(
        "--group",
        action="append",
        dest="groups",
        help="Группа источников. Можно повторять. По умолчанию — все.",
    )
    catchup.add_argument(
        "--from",
        dest="date_from",
        type=_parse_date,
        default=None,
        help="Начало диапазона. По умолчанию — начало окна.",
    )
    catchup.add_argument(
        "--till",
        dest="date_till",
        type=_parse_date,
        default=None,
        help="Конец диапазона. По умолчанию — конец окна.",
    )

    sub.add_parser("catchup-status", help="Ход идущего догона")
    sub.add_parser("catchup-stop", help="Остановить догон после текущей сессии")

    cov = sub.add_parser("coverage", help="Состояние данных по группам источников")
    cov.add_argument("--asof", type=_parse_date, default=None, help="Дата решения")

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


def _worker(
    path: str,
    method: str = "GET",
    json: dict[str, object] | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """Обратиться к внутреннему интерфейсу worker'а.

    Команда — тонкий клиент: сбор идёт фоновой задачей в worker, а не в этом
    процессе. Иначе «следить» означало бы смотреть в собственный терминал, а
    интерфейс, когда появится, управлять догоном не смог бы.
    """
    settings = get_settings()
    url = f"{settings.worker_internal_url.rstrip('/')}/internal{path}"
    return httpx.request(
        method,
        url,
        json=json,
        params=params,
        timeout=settings.worker_sync_timeout_seconds,
    )


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message", detail))
    return str(detail or body)


def _catchup(groups: list[str] | None, date_from: dt.date | None, date_till: dt.date | None) -> int:
    """Запустить догон.

    Код 2 отделён от 1 намеренно: «догнали не всё» и «не смогли начать» —
    разные неисправности с разными последствиями.
    """
    payload: dict[str, object] = {}
    if groups:
        payload["groups"] = groups
    if date_from is not None:
        payload["date_from"] = date_from.isoformat()
    if date_till is not None:
        payload["date_till"] = date_till.isoformat()

    try:
        response = _worker("/catchup", "POST", json=payload)
    except httpx.HTTPError as error:
        print(f"worker недоступен: {error}")
        return 1

    if response.status_code != httpx.codes.OK:
        print(f"догон не запущен: {_detail(response)}")
        return 1

    body = response.json()
    if body.get("status") == "idle":
        print(body.get("reason", "пропущенных сессий нет"))
        return 0

    print(f"догон запущен: сессий {body['requested_sessions']}")
    print(f"  группы:   {', '.join(body['groups'])}")
    print(f"  диапазон: {body['date_from']} … {body['date_till']}")
    if body.get("clamped"):
        print("  диапазон обрезан по окну набора")
    print("ход: financial_ai.market_data.cli catchup-status")
    return 0


def _catchup_status() -> int:
    try:
        response = _worker("/catchup")
    except httpx.HTTPError as error:
        print(f"worker недоступен: {error}")
        return 1

    body = response.json()
    print(f"состояние: {body['status']}")
    if body["status"] == "idle":
        return 0

    print(f"  сессий:    {body['requested']}")
    print(f"  закрыто:   {body['closed']}")
    print(f"  осталось:  {body['remaining']}")
    if body["failed"]:
        print(f"  незакрыто: {body['failed']}")
    if body["current"]:
        print(f"  сейчас:    {body['current']}")
    if body["reason"]:
        print(f"  причина:   {body['reason']}")
    return 2 if body["failed"] else 0


def _catchup_stop() -> int:
    try:
        response = _worker("/catchup", "DELETE")
    except httpx.HTTPError as error:
        print(f"worker недоступен: {error}")
        return 1

    body = response.json()
    if body["status"] == "idle":
        print("останавливать нечего")
        return 0
    print(f"остановка запрошена; текущая сессия {body['current']} доводится до конца")
    return 0


def _coverage(asof: dt.date | None) -> int:
    """Состояние данных по группам.

    Расхождение между покрытием и долей значений выделяется явно: именно оно
    означает «собрано, но пусто», и именно его человек не заметит в таблице
    чисел.
    """
    params = {"asof": asof.isoformat()} if asof else None
    try:
        response = _worker("/coverage", params=params)
    except httpx.HTTPError as error:
        print(f"worker недоступен: {error}")
        return 1

    if response.status_code != httpx.codes.OK:
        print(f"сводка не получена: {_detail(response)}")
        return 1

    body = response.json()
    print(f"состояние на {body['asof_date']}")
    print()
    print(f"{'группа':<14}{'окно':>6}{'покрыто':>9}{'доля':>8}{'значений':>10}  период")

    suspicious: list[str] = []
    for row in body["groups"]:
        if row["has_history"]:
            window = str(row["window_sessions"])
            covered = str(row["sessions_covered"])
            share = f"{row['coverage_ratio'] * 100:.1f}%"
            period = f"{row['period_from'] or '—'} … {row['period_till'] or '—'}"
        else:
            window = covered = share = "—"
            period = "справочник, истории нет"

        values = "—" if row["value_ratio"] is None else f"{row['value_ratio'] * 100:.1f}%"
        print(f"{row['group']:<14}{window:>6}{covered:>9}{share:>8}{values:>10}  {period}")

        if (
            row["has_history"]
            and row["coverage_ratio"]
            and row["value_ratio"] is not None
            and row["value_ratio"] < 0.01
        ):
            suspicious.append(row["group"])

    for group in suspicious:
        print()
        print(f"внимание: у группы {group} покрытие есть, а значений нет — собрано пустым")

    return 0


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
        print(f"кривая доходности: рядов {len(zcyc)}")
        for series_id in sorted(zcyc):
            print(f"   {series_id}: {zcyc[series_id]}")
        if not zcyc:
            print("   ПУСТО — страница отвечает, но точек за эту дату нет")
            ok = False

        # Модель объявляет эти точки входом (`yield_curve_points`), и без них
        # наклон кривой не считается. Параметры модели Нельсона-Сигеля, которые
        # собирались раньше, на их месте были бы незаметны: рядов столько же.
        required = [f"{cbr.ZCYC_SERIES_PREFIX}{term}" for term in cbr.REQUIRED_ZCYC_TERMS]
        missing = [series_id for series_id in required if series_id not in zcyc]
        if missing:
            print(f"   ОТКАЗ — нет точек, объявленных моделью: {', '.join(missing)}")
            ok = False
        else:
            print(f"   точки модели на месте: {', '.join(cbr.REQUIRED_ZCYC_TERMS)}")
    except cbr.CbrError as error:
        print(f"кривая доходности: ОТКАЗ — {error}")
        ok = False

    if not ok:
        print()
        print("Разбор не сошёлся с живой страницей: вёрстка ЦБ могла измениться.")
        return 1
    return 0


async def _verify_brent(session_date: dt.date) -> int:
    """Обратиться к срочному рынку по-настоящему и показать выбранный контракт.

    Ловит ровно тот дефект, который прожил незамеченным: адрес собирался с
    доской акций, сочетания такого на бирже нет, и в лог каждую сессию шло
    «подходящего контракта не нашлось» — как обычное отсутствие данных.
    """
    settings = get_settings()
    async with IssClient(ingest.build_iss_config(settings)) as iss:
        rows = await iss.fetch_session_rows_for(
            session_date.isoformat(),
            brent.COLUMNS,
            engine=brent.ENGINE,
            market=brent.MARKET,
        )

    print(f"срочный рынок за {session_date}: строк {len(rows)}")
    print(f"HTTP ISS: {iss.metrics.to_dict()}")
    if not rows:
        print("ПУСТО — раздел отвечает, но строк за эту дату нет.")
        print("Проверьте, что адрес строится БЕЗ сегмента доски.")
        return 1

    contract = brent.select_front_contract(rows, session_date)
    if contract is None:
        print("ОТКАЗ — подходящего контракта не нашлось среди полученных строк")
        return 1

    print(f"фронтальный контракт: {contract.secid}, срок {contract.expiry}")
    print(f"закрытие: {contract.close}")
    if contract.close is None:
        print("ОТКАЗ — контракт найден, но цены в нём нет")
        return 1
    return 0


async def _verify_positions(session_date: dt.date, ticker: str) -> int:
    """Обратиться к источнику позиций по-настоящему.

    Подтверждает на настоящем ответе три вещи разом: соответствие акции и
    контракта построилось, обмен формой состоялся, разбор дал значения.
    Подделка этого по определению не ловит.
    """
    settings = get_settings()
    share = ticker.strip().upper()

    async with (
        IssClient(ingest.build_iss_config(settings)) as iss,
        PositionsClient(settings) as client,
    ):
        candidates = await links.build_candidates(iss)
        candidate = candidates.get(share)
        print(f"соответствий акций и контрактов: {len(candidates)}")
        if candidate is None:
            print(f"ОТКАЗ — для {share} контракта в списке ISS нет")
            return 1
        contract = candidate.contract_code
        print(
            f"{share}: контракт {contract} "
            f"(серия для сверки эмитента {candidate.probe_secid}, "
            f"открытый интерес {candidate.open_interest})"
        )

        known = await client.known_contracts(session_date)
        print(f"инструментов на странице: {len(known)}")
        if contract not in known:
            print(f"ОТКАЗ — {contract} страница открытых позиций не знает")
            return 1

        snapshot = await client.fetch(contract, session_date)
        spent = client.requests_made

    if snapshot is None:
        print(f"ПУСТО — за {session_date} снимка нет (выходной или данные не опубликованы)")
        return 1

    print(f"дата данных: {snapshot.trade_date}")
    print(f"   ФИЗ длинные {snapshot.fiz_long}, короткие {snapshot.fiz_short}")
    print(f"   ЮР  длинные {snapshot.jur_long}, короткие {snapshot.jur_short}")
    # Цена сверки в обращениях: единица обращения здесь — инструмент и дата,
    # и знать её важнее, чем по любому другому источнику.
    print(f"обращений к источнику: {spent}")
    if not snapshot.has_values:
        print("ОТКАЗ — ответ разобран, но значений в нём нет")
        return 1
    return 0


async def _verify_reference(session_date: dt.date) -> int:
    """Обратиться к разделу аналитики индексов по-настоящему.

    Проверяет то, чего подделка проверить не может: что веса и отрасли вообще
    приходят со значениями. Оба справочника были сломаны именно так — ответ
    успешный, строки есть, значений в них нет.
    """
    settings = get_settings()
    ok = True

    async with IssClient(ingest.build_iss_config(settings)) as iss:
        rows = await iss.fetch_index_analytics("IMOEX", session_date.isoformat())
        weights = reference.rows_to_weights(rows, session_date, "IMOEX")
        print(f"состав IMOEX за {session_date}: строк {len(rows)}, с весом {len(weights)}")
        for series_id in sorted(weights)[:5]:
            print(f"   {series_id}: {weights[series_id][session_date]}")
        if not weights:
            print("   ОТКАЗ — весов нет. Проверьте, что запрос идёт в раздел аналитики")
            ok = False

        titles = await iss.fetch_index_titles()
        print(f"имён индексов получено: {len(titles)}")

        covered: dict[str, str] = {}
        for index_id in reference.SECTOR_INDEX_IDS:
            composition = await iss.fetch_index_analytics(index_id)
            named = titles.get(index_id) or index_id
            print(f"   {index_id:<8} {named:<28} бумаг {len(composition)}")
            for row in composition:
                ticker = row.get("ticker")
                if isinstance(ticker, str) and ticker.strip():
                    covered.setdefault(ticker.strip().upper(), index_id)

    print(f"бумаг с отраслью: {len(covered)}")
    if not covered:
        print("ОТКАЗ — ни одна бумага не отнесена к отрасли")
        ok = False

    if not ok:
        print()
        print("Справочники не сошлись с живым ответом: разметка раздела могла измениться.")
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
    if args.command == "verify-brent":
        return asyncio.run(_verify_brent(args.session))
    if args.command == "verify-positions":
        return asyncio.run(_verify_positions(args.session, args.ticker))
    if args.command == "verify-reference":
        return asyncio.run(_verify_reference(args.session))
    if args.command == "gaps":
        return asyncio.run(_gaps(args.asof))
    if args.command == "catchup":
        return _catchup(args.groups, args.date_from, args.date_till)
    if args.command == "catchup-status":
        return _catchup_status()
    if args.command == "catchup-stop":
        return _catchup_stop()
    if args.command == "coverage":
        return _coverage(args.asof)
    if args.command == "backfill":
        return asyncio.run(_backfill(args.date_from, args.ticker))
    return 1


if __name__ == "__main__":
    sys.exit(main())
