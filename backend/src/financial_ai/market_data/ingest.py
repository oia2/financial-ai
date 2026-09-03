"""Цикл сбора рыночных данных.

Порядок шагов взят из оркестратора исследовательского репозитория
(`pipelines/auto_update/cli.py`), а не изобретён: календарь идёт первым и
гейтит всё остальное, котировки задают пространство строк, прочие источники
на него накладываются.

Ключевое правило: **данные незавершённой сессии в хранилище не попадают**.
Модель наблюдает последнюю завершённую сессию `t` и торгует на открытии `t+1`;
собрать раньше закрытия значит завести утечку будущего в признаки.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import gaps
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.iss.client import IssClient, IssConfig, IssError
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import (
    brent,
    cbr,
    dividends,
    equity_agg,
    equity_d1,
    global_series,
    positions,
    reference,
    trading_calendar,
)

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
# Сколько раз пробовать добрать задержанный источник в пределах прогона.
# Позиции по фьючерсам приходят позже закрытия; если не успели — наблюдение
# остаётся наблюдением о своём дне, а не переезжает на следующий.
DELAYED_SOURCE_ATTEMPTS = 3
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass(slots=True)
class SourceOutcome:
    """Исход сбора по одному источнику."""

    source_id: str
    status: str
    rows_written: int = 0
    failure_reason: str | None = None


@dataclass(slots=True)
class IngestResult:
    """Исход всего прогона."""

    run_id: str
    session_date: dt.date | None
    outcomes: list[SourceOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return all(o.status != STATUS_FAILED for o in self.outcomes)

    @property
    def unfinished_sources(self) -> list[str]:
        """Источники, оставшиеся незакрытыми: видны без чтения логов."""
        return [o.source_id for o in self.outcomes if o.status == STATUS_FAILED]


TRIGGER_DAILY = "daily"
TRIGGER_CATCHUP = "catchup"


@dataclass(slots=True)
class CatchupResult:
    """Исход догона пропущенных сессий."""

    requested: list[dt.date] = field(default_factory=list)
    closed: list[dt.date] = field(default_factory=list)
    failed: list[dt.date] = field(default_factory=list)

    # Хранилище пусто: это не дыра, а отсутствие истории. Догон намеренно не
    # выполнялся — нужна первичная загрузка.
    needs_backfill: bool = False
    skipped_reason: str | None = None

    @property
    def attempted(self) -> bool:
        return bool(self.requested)


def build_iss_config(settings: Settings) -> IssConfig:
    return IssConfig(
        base_url=settings.market_data_iss_base_url,
        board=settings.market_data_board,
        page_limit=settings.market_data_iss_page_limit,
        retries=settings.market_data_http_retries,
        timeout_seconds=settings.market_data_http_timeout_seconds,
    )


async def ingest_session(
    session: AsyncSession,
    settings: Settings,
    session_date: dt.date | None = None,
    client: IssClient | None = None,
    cbr_client: httpx.AsyncClient | None = None,
    broker_client: object | None = None,
) -> IngestResult:
    """Собрать данные одной торговой сессии.

    Если дата не задана, берётся последняя завершённая сессия календаря.
    """
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)
    run_id = str(uuid.uuid4())
    result = IngestResult(run_id=run_id, session_date=session_date)

    config = build_iss_config(settings)
    owns_client = client is None
    iss = client or IssClient(config)
    if owns_client:
        await iss.__aenter__()

    try:
        # Шаг 1: календарь. До него неизвестно, была ли сессия вообще.
        calendar_outcome = await _run_source(
            repository,
            run_id,
            trading_calendar.SOURCE_ID,
            session_date,
            lambda: trading_calendar.sync_trading_calendar(
                iss, repository, settings.market_data_calendar_proxy_security
            ),
        )
        result.outcomes.append(calendar_outcome)
        await session.commit()

        if session_date is None:
            session_date = await calendar.latest_session(moscow_today())
            result.session_date = session_date

        if session_date is None:
            logger.warning("сбор: торговых сессий в календаре нет, собирать нечего")
            return result

        # Гейт: в неторговый день на биржу не ходим вовсе.
        if not await calendar.is_session(session_date):
            logger.info("сбор: %s не является торговой сессией, пропуск", session_date)
            outcome = SourceOutcome(
                source_id=equity_d1.SOURCE_ID,
                status=STATUS_SKIPPED,
                failure_reason="не торговая сессия",
            )
            result.outcomes.append(outcome)
            await _record(repository, run_id, outcome, session_date)
            await session.commit()
            return result

        # Шаг 2: котировки. Они задают пространство строк, поэтому идут
        # раньше всего, что на него накладывается.
        quotes_outcome = await _run_source(
            repository,
            run_id,
            equity_d1.SOURCE_ID,
            session_date,
            lambda: equity_d1.sync_equity_daily(iss, repository, session_date),
        )
        result.outcomes.append(quotes_outcome)
        await session.commit()

        # Шаги 3+: остальные источники. Порядок из оркестратора
        # исследовательского репозитория; неудача одного не отменяет прочие.
        for source_id, action in (
            (
                equity_agg.SOURCE_ID,
                lambda: equity_agg.sync_equity_aggregates(iss, repository, session_date),
            ),
            (
                global_series.SOURCE_ID,
                lambda: global_series.sync_iss_series(iss, repository, session_date),
            ),
            (
                reference.SECTORS_SOURCE_ID,
                lambda: reference.sync_sectors(iss, repository, session_date),
            ),
            (
                reference.CONSTITUENTS_SOURCE_ID,
                lambda: reference.sync_index_constituents(iss, repository, session_date),
            ),
            (brent.SOURCE_ID, lambda: brent.sync_brent(iss, repository, session_date)),
            (cbr.SOURCE_ID, lambda: _sync_cbr(repository, session_date, cbr_client)),
            (
                dividends.SOURCE_ID,
                lambda: _sync_dividends(repository, session_date, broker_client),
            ),
        ):
            outcome = await _run_source(repository, run_id, source_id, session_date, action)
            result.outcomes.append(outcome)
            await session.commit()

        # Задержанный источник — отдельно и с повторами.
        delayed = await _run_delayed_source(
            repository,
            run_id,
            positions.SOURCE_ID,
            session_date,
            lambda: positions.sync_positions(iss, repository, session_date),
        )
        result.outcomes.append(delayed)
        await session.commit()

        return result
    finally:
        if owns_client:
            await iss.__aexit__(None, None, None)


async def catch_up(
    session: AsyncSession,
    settings: Settings,
    asof_date: dt.date,
    client: IssClient | None = None,
    cbr_client: httpx.AsyncClient | None = None,
) -> CatchupResult:
    """Догнать пропущенные сессии окна.

    Три свойства, ради которых написано именно так:

    - **сессии идут от старых к новым.** При прерывании остаётся закрытым более
      ранний участок окна, а не разрозненные даты;
    - **недоступность одной сессии не отменяет остальные.** Задержанные
      модальности за старые даты могут быть недоступны по своей природе;
    - **порога по числу сессий нет.** Догоняется всё окно: система работает с
      314 сессиями за раз, и остановка на десятом дне сделала бы механизм
      бесполезным ровно в том случае, ради которого он заведён.
    """
    result = CatchupResult()

    if not settings.market_data_catchup_enabled:
        result.skipped_reason = "догон выключен настройкой"
        return result

    report = await gaps.find_gaps(session, settings, asof_date)

    if report.needs_backfill:
        # Разграничение по состоянию хранилища, а не по числу пропущенных дней:
        # на чистой базе календарь уже полон, и «пропущено 314 сессий» —
        # нормальное состояние новой установки, а не авария.
        result.needs_backfill = True
        result.skipped_reason = "в хранилище нет наблюдений: нужна первичная загрузка"
        logger.warning("догон не выполняется: %s", result.skipped_reason)
        return result

    if not report.missing_sessions:
        return result

    result.requested = list(report.missing_sessions)
    logger.info(
        "догон: пропущено сессий %d, окно %d",
        len(result.requested),
        len(report.window),
    )

    repository = MarketDataRepository(session)
    owns_client = client is None
    iss = client or IssClient(build_iss_config(settings))
    if owns_client:
        await iss.__aenter__()

    try:
        for day in result.requested:
            run_id = str(uuid.uuid4())
            outcomes = await _catch_up_session(repository, run_id, iss, day)
            await session.commit()

            # Сессия считается закрытой по котировкам: на них держится
            # пространство строк. Недоступность задержанной модальности за
            # старую дату — нормальное явление, а не незакрытая сессия.
            quotes = next(o for o in outcomes if o.source_id == equity_d1.SOURCE_ID)
            if quotes.status == STATUS_FAILED:
                result.failed.append(day)
            else:
                result.closed.append(day)

        # Диапазонные источники закрывают дыру одним обращением на ряд: биржа и
        # ЦБ отдают историю за период, и перебор дат здесь был бы чистой
        # потерей.
        await _catch_up_ranges(
            repository, iss, result.requested[0], result.requested[-1], cbr_client
        )
        await session.commit()
    finally:
        if owns_client:
            await iss.__aexit__(None, None, None)

    logger.info("догон: закрыто %d из %d", len(result.closed), len(result.requested))
    return result


async def _catch_up_session(
    repository: MarketDataRepository,
    run_id: str,
    iss: IssClient,
    session_date: dt.date,
) -> list[SourceOutcome]:
    """Собрать одну пропущенную сессию источниками с выборкой по дате.

    Справочник секторов сюда не входит: он отражает **текущую** принадлежность,
    истории у него нет, и догонять там нечего. Дивиденды тоже: они собираются по
    активу за всю историю сразу, поэтому ежедневный прогон уже покрывает
    пропущенные дни.
    """
    outcomes: list[SourceOutcome] = []

    for source_id, action in (
        (
            equity_d1.SOURCE_ID,
            lambda: equity_d1.sync_equity_daily(iss, repository, session_date),
        ),
        (
            equity_agg.SOURCE_ID,
            lambda: equity_agg.sync_equity_aggregates(iss, repository, session_date),
        ),
        (
            reference.CONSTITUENTS_SOURCE_ID,
            lambda: reference.sync_index_constituents(iss, repository, session_date),
        ),
        (brent.SOURCE_ID, lambda: brent.sync_brent(iss, repository, session_date)),
        (
            positions.SOURCE_ID,
            lambda: positions.sync_positions(iss, repository, session_date),
        ),
    ):
        outcome = await _run_source(
            repository, run_id, source_id, session_date, action, trigger=TRIGGER_CATCHUP
        )
        outcomes.append(outcome)

    return outcomes


async def _catch_up_ranges(
    repository: MarketDataRepository,
    iss: IssClient,
    date_from: dt.date,
    date_till: dt.date,
    cbr_client: httpx.AsyncClient | None,
) -> None:
    """Закрыть дыру источниками, умеющими выборку за период.

    Число обращений здесь не зависит от длины дыры. Прогон записывается на
    дату конца периода: он относится ко всему промежутку, а не к одной сессии.
    """
    run_id = str(uuid.uuid4())

    await _run_source(
        repository,
        run_id,
        global_series.SOURCE_ID,
        date_till,
        lambda: global_series.sync_iss_series_range(iss, repository, date_from, date_till),
        trigger=TRIGGER_CATCHUP,
    )
    await _run_source(
        repository,
        run_id,
        cbr.SOURCE_ID,
        date_till,
        lambda: _sync_cbr_range(repository, date_from, date_till, cbr_client),
        trigger=TRIGGER_CATCHUP,
    )


async def ingest_and_rank(
    session: AsyncSession,
    settings: Settings,
    session_date: dt.date | None = None,
    client: IssClient | None = None,
    cbr_client: httpx.AsyncClient | None = None,
    broker_client: object | None = None,
) -> tuple[IngestResult, object | None]:
    """Собрать данные сессии, материализовать набор и запросить ранжирование.

    Сбой ранжирования **не отменяет** собранные данные: они уже сохранены, и
    повторить можно только запрос. Поэтому исход ранжирования возвращается
    отдельно от исхода сбора.
    """
    # Импорт здесь, а не в шапке: сбор данных не должен зависеть от звена
    # ранжирования — оно может отсутствовать, и это не мешает собирать.
    from financial_ai.ranking import client as ranking_client
    from financial_ai.ranking import dataset as dataset_module

    result = await ingest_session(
        session, settings, session_date, client, cbr_client, broker_client
    )
    if result.session_date is None or not result.succeeded:
        logger.info("ранжирование пропущено: сбор не завершён успешно")
        return result, None

    # Догон идёт ПОСЛЕ текущей сессии и ДО материализации набора. Порядок не
    # косметический: текущая сессия гейтит ранжирование, и длинный догон не
    # должен её задерживать; а набор, собранный до догона, уехал бы к модели с
    # дырами и породил бы второй набор с другим дайджестом.
    await catch_up(session, settings, result.session_date, client, cbr_client)

    try:
        dataset = await dataset_module.build_dataset(session, settings, result.session_date)
        ranking = await ranking_client.request_ranking(settings, dataset)
    except dataset_module.DatasetError as error:
        logger.warning("набор на %s не собран: %s", result.session_date, error)
        return result, None
    except ranking_client.RankingUnavailableError as error:
        # Отдельная ветка не для красоты: сбой ранжирования и сбой сбора —
        # разные неисправности, и смешивать их в отчёте нельзя.
        logger.warning("ранжирование на %s не получено: %s", result.session_date, error)
        return result, None

    dataset_module.prune_datasets(settings)
    return result, ranking


async def _sync_cbr(
    repository: MarketDataRepository,
    session_date: dt.date,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Дневные макроряды ЦБ за сессию.

    Режим доступности у них иной: по time_semantics.md они публикуются ДО
    закрытия сессии и уже относятся к дню `t`. Поэтому запрашиваются за ту же
    дату, а не за предыдущую.
    """
    config = cbr.CbrConfig()
    written = 0

    key_rate = await cbr.fetch_key_rate(config, session_date, session_date, client)
    written += await repository.upsert_global_values(cbr.KEY_RATE_SERIES_ID, key_rate)

    zcyc = await cbr.fetch_zcyc(config, session_date, session_date, client)
    for series_id, values in zcyc.items():
        written += await repository.upsert_global_values(series_id, values)

    return written


async def _sync_dividends(
    repository: MarketDataRepository,
    session_date: dt.date,
    broker_client: object | None,
) -> int:
    """Дивиденды от брокера.

    Единственный источник, которому нужен токен. Если брокер не настроен —
    источник пропускается: дивиденды дополняют картину, но не являются
    основанием для отказа всего сбора.
    """
    if broker_client is None:
        raise IssError("клиент брокера не настроен: дивиденды пропущены")

    known = await repository.tickers_with_history()
    return await dividends.sync_dividends(broker_client, repository, session_date, known)


async def _sync_cbr_range(
    repository: MarketDataRepository,
    date_from: dt.date,
    date_till: dt.date,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Макроряды ЦБ за период.

    Страницы Банка России уже принимают границы периода, поэтому дыра любой
    длины закрывается двумя обращениями — по одному на ключевую ставку и ЗКЦ.
    """
    config = cbr.CbrConfig()
    written = 0

    key_rate = await cbr.fetch_key_rate(config, date_from, date_till, client)
    written += await repository.upsert_global_values(cbr.KEY_RATE_SERIES_ID, key_rate)

    zcyc = await cbr.fetch_zcyc(config, date_from, date_till, client)
    for series_id, values in zcyc.items():
        written += await repository.upsert_global_values(series_id, values)

    return written


async def _run_delayed_source(
    repository: MarketDataRepository,
    run_id: str,
    source_id: str,
    session_date: dt.date,
    action: object,
) -> SourceOutcome:
    """Выполнить сбор задержанного источника с повторами.

    Данные приходят позже закрытия сессии. Если после всех попыток их всё ещё
    нет — исход `failed`, и наблюдение просто отсутствует. Записать его датой
    следующей сессии НЕЛЬЗЯ: это исказило бы историю необратимо и незаметно.
    """
    outcome = SourceOutcome(source_id, STATUS_FAILED, failure_reason="не выполнялся")
    for attempt in range(1, DELAYED_SOURCE_ATTEMPTS + 1):
        outcome = await _run_source(repository, run_id, source_id, session_date, action)
        if outcome.status == STATUS_OK:
            return outcome
        logger.info(
            "задержанный источник %s: попытка %d из %d не дала данных",
            source_id,
            attempt,
            DELAYED_SOURCE_ATTEMPTS,
        )
    return outcome


async def _run_source(
    repository: MarketDataRepository,
    run_id: str,
    source_id: str,
    session_date: dt.date | None,
    action: object,
    trigger: str = TRIGGER_DAILY,
) -> SourceOutcome:
    """Выполнить сбор одного источника, зафиксировав исход.

    Неуспех одного источника не отменяет успех остальных и не затрагивает
    ранее собранные данные: исключение ловится здесь и записывается.
    """
    started = dt.datetime.now(dt.UTC)
    try:
        written = await action()  # type: ignore[operator]
    except IssError as error:
        outcome = SourceOutcome(source_id, STATUS_FAILED, failure_reason=str(error))
        logger.warning("сбор: источник %s не удался: %s", source_id, error)
    except Exception as error:
        outcome = SourceOutcome(source_id, STATUS_FAILED, failure_reason=repr(error))
        logger.exception("сбор: источник %s завершился ошибкой", source_id)
    else:
        outcome = SourceOutcome(source_id, STATUS_OK, rows_written=int(written))

    await repository.record_run(
        run_id=run_id,
        source_id=source_id,
        status=outcome.status,
        started_at=started,
        finished_at=dt.datetime.now(dt.UTC),
        session_date=session_date,
        rows_written=outcome.rows_written,
        failure_reason=outcome.failure_reason,
        trigger=trigger,
    )
    return outcome


async def _record(
    repository: MarketDataRepository,
    run_id: str,
    outcome: SourceOutcome,
    session_date: dt.date | None,
) -> None:
    now = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id=run_id,
        source_id=outcome.source_id,
        status=outcome.status,
        started_at=now,
        finished_at=now,
        session_date=session_date,
        rows_written=outcome.rows_written,
        failure_reason=outcome.failure_reason,
    )
