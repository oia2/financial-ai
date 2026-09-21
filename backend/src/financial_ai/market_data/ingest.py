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
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, gaps, groups, links, plan
from financial_ai.market_data.calendar import MOSCOW, TradingCalendar, moscow_today
from financial_ai.market_data.interrupt import SourcePartialError, SourceStoppedError
from financial_ai.market_data.iss.client import IssClient, IssConfig, IssError
from financial_ai.market_data.repository import CURRENT_COVERAGE_VERSION, MarketDataRepository
from financial_ai.market_data.sources import (
    brent,
    cbr,
    equity_agg,
    equity_d1,
    global_series,
    positions,
    reference,
    securities,
    trading_calendar,
)
from financial_ai.market_data.sources.positions_client import PositionsClient
from financial_ai.market_data.verification import (
    VerificationResult,
    WorkEvidence,
    required_work_keys,
)

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
# Сколько раз пробовать добрать задержанный источник в пределах прогона.
# Позиции по фьючерсам приходят позже закрытия; если не успели — наблюдение
# остаётся наблюдением о своём дне, а не переезжает на следующий.
DELAYED_SOURCE_ATTEMPTS = 3
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
# Источник прерван командой остановки. Отдельный исход, а не «ок»: успешный
# прогон закрывает сессию по источнику, и прерванный сбор закрывал бы её,
# спросив три инструмента из ста двадцати (FR-050).
STATUS_STOPPED = "stopped"
# Источник, которого этот прогон не спрашивает вовсе: суточный, уже спрошенный
# сегодня. В план прогона он не попадает — план показывает то, что прогон
# делает, а не перечень всего, что бывает (FR-007).
STATUS_OMITTED = "omitted"
# Исходы, при которых источник закрытым не считается.
_UNFINISHED = frozenset({STATUS_FAILED, STATUS_STOPPED})


@dataclass(slots=True)
class SourceOutcome:
    """Исход сбора по одному источнику."""

    source_id: str
    status: str
    rows_written: int = 0
    failure_reason: str | None = None

    # Чем закончился источник — словами, для экрана. У неудачи это причина, у
    # успеха — что он принёс: «243 бумаги». Прежде переносилась только
    # причина, и в обычном прогоне лента стояла без единой подписи (FR-058f).
    detail: str | None = None
    counts_as_unavailable: bool = False

    @property
    def shown(self) -> str | None:
        """Подпись источника на экране."""
        return self.failure_reason or self.detail


@dataclass(slots=True)
class IngestResult:
    """Исход всего прогона."""

    run_id: str
    session_date: dt.date | None
    outcomes: list[SourceOutcome] = field(default_factory=list)

    @property
    def interrupted(self) -> bool:
        """План сессии не доработан по команде остановки.

        Отдельно от «не собрана»: прерванную сессию доделывает продолжение, а
        несобранную доберёт обычный план по правилам повторов (FR-058).
        """
        return any(outcome.status == STATUS_STOPPED for outcome in self.outcomes)

    @property
    def succeeded(self) -> bool:
        # Прерванный источник считается незакрытым наравне с упавшим: сессия,
        # в которой спросили три бумаги из ста двадцати, собранной не является
        # (FR-050).
        return all(o.status not in _UNFINISHED for o in self.outcomes)

    @property
    def unfinished_sources(self) -> list[str]:
        """Источники, оставшиеся незакрытыми: видны без чтения логов."""
        return [o.source_id for o in self.outcomes if o.status in _UNFINISHED]


TRIGGER_DAILY = "daily"
TRIGGER_CATCHUP = "catchup"


# Исход сессии, чей план не доработан по команде остановки. Третье значение
# рядом с «собрана» и «не собрана»: прерванную сессию доделывает продолжение, а
# несобранную — обычный план по правилам повторов (FR-058).
INTERRUPTED = "interrupted"


@dataclass(slots=True)
class CatchupResult:
    """Исход догона пропущенных сессий."""

    requested: list[dt.date] = field(default_factory=list)
    closed: list[dt.date] = field(default_factory=list)
    failed: list[dt.date] = field(default_factory=list)

    # Сессии, чей план прерван командой: их доделывает продолжение.
    interrupted: list[dt.date] = field(default_factory=list)

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
    positions_client: PositionsClient | None = None,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    run_id: str | None = None,
) -> IngestResult:
    """Собрать данные одной торговой сессии.

    Если дата не задана, берётся последняя завершённая сессия календаря.

    ``run_id`` передаётся, когда сессия — часть прогона из нескольких сессий.
    Идентификатор один на весь прогон: журнал группирует исходы по нему и
    считает в прогоне сессии, а при идентификаторе на сессию отставание в
    восемьдесят дней показывалось восемьюдесятью прогонами по одному дню
    (FR-052).
    """
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)
    run_id = run_id or str(uuid.uuid4())
    result = IngestResult(run_id=run_id, session_date=session_date)

    config = build_iss_config(settings)
    owns_client = client is None
    iss = client or IssClient(config)
    if owns_client:
        await iss.__aenter__()

    # Позиции ходят не в биржевой интерфейс данных, а формой на сайт биржи,
    # поэтому у них свой клиент. Создаётся здесь по тому же правилу, что и
    # клиент ISS: вызывающий может подменить его, но не обязан — иначе источник
    # молча не собирался бы у каждого, кто про этот аргумент не знает.
    owns_positions = positions_client is None
    pos_client = positions_client or PositionsClient(settings)
    if owns_positions:
        await pos_client.__aenter__()

    # Повторы клиент ведёт сам — шесть попыток с нарастающей паузой, — и без
    # этого признака остановка ждала их все (FR-058j).
    if hasattr(iss, "should_stop"):
        iss.should_stop = should_stop

    try:
        # Шаг 1: календарь. До него неизвестно, была ли сессия вообще.
        #
        # Спрашивается по суточному гейту, а не на каждую сессию. Прежде запрос
        # шёл безусловно, и догон десяти дней тянул историю торгов с 1990 года
        # десять раз подряд — это и была заметная часть «подвисаний».
        from financial_ai.market_data.advance import calendar_is_due

        if await calendar_is_due(repository, None):
            calendar_outcome = await run_source(
                repository,
                run_id,
                trading_calendar.SOURCE_ID,
                session_date,
                lambda: trading_calendar.sync_trading_calendar(
                    iss, repository, settings.market_data_calendar_proxy_security
                ),
                on_source=on_source,
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

        if should_stop is not None and should_stop():
            logger.info("сбор сессии %s прерван до опознания бумаг", session_date)
            closed = await completeness.closed_sources_for(repository, session_date)
            pending_source = next(
                (
                    source_id
                    for source_id in sorted(_DAILY_SESSION_SOURCES)
                    if source_id not in closed
                ),
                None,
            )
            if pending_source is not None:
                outcome = SourceOutcome(
                    source_id=pending_source,
                    status=STATUS_STOPPED,
                    failure_reason="остановлено до запроса",
                )
                result.outcomes.append(outcome)
                await _record(repository, run_id, outcome, session_date)
                await session.commit()
            return result

        # Шаг 2: опознание бумаг. ДО котировок, а не после: ключом наблюдения
        # служит сущность, а не имя, и в сессию переименования наблюдение,
        # записанное раньше сверки, заводило вторую бумагу с оборванной
        # историей (FR-048).
        alias_events = await _sync_aliases(repository, iss, session_date)
        await session.commit()

        # Шаг 3: котировки. Они задают пространство строк, поэтому идут
        # раньше всего, что на него накладывается.
        closed = await completeness.closed_sources_for(repository, session_date)

        # Известное о сессии объявляется СРАЗУ, а не когда до источника дойдёт
        # очередь: что суточный справочник сегодня не спрашивается и что
        # источник за эту сессию закрыт, известно до первого обращения. Иначе
        # лента показывает ожидающими строки, которых в работе нет, и «пляшет»,
        # пока прогон доходит до каждой (FR-058l).
        if on_source is not None:
            for source_id in (reference.SECTORS_SOURCE_ID, securities.SOURCE_ID):
                if not await reference_is_due(repository, source_id, session_date):
                    on_source(source_id, STATUS_OMITTED, None)
            # Только те, кого эта сессия и собирает. Диапазонный источник
            # идёт раз на прогон и свой исход уже объявил: сказать про него
            # «собран ранее» значило бы затереть «100 рядов» словами ни о чём.
            for source_id in sorted(closed & _DAILY_SESSION_SOURCES):
                outcome = _already_collected(source_id)
                on_source(source_id, outcome.status, outcome)

        if equity_d1.SOURCE_ID in closed:
            quotes_outcome = _already_collected(equity_d1.SOURCE_ID)
        else:
            quotes_outcome = await run_source(
                repository,
                run_id,
                equity_d1.SOURCE_ID,
                session_date,
                lambda: equity_d1.sync_equity_daily(iss, repository, session_date),
                on_source=on_source,
            )
        result.outcomes.append(quotes_outcome)
        await session.commit()

        # Источники, собранные за эту сессию раньше. Прогон, вернувшийся к
        # недобранной сессии, добирает недостающее, а не проходит круг заново:
        # на стенде 2026-09-19 котировки за одну сессию спрашивались трижды,
        # все три раза успешно (FR-058e, FR-022).
        collected = closed

        # Шаги 4+: остальные источники. Порядок из оркестратора
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
                reference.CONSTITUENTS_SOURCE_ID,
                lambda: reference.sync_index_constituents(iss, repository, session_date),
            ),
            (brent.SOURCE_ID, lambda: brent.sync_brent(iss, repository, session_date)),
            (
                cbr.SOURCE_ID,
                lambda: _sync_cbr(repository, session_date, cbr_client, should_stop),
            ),
        ):
            # Остановка проверяется между обращениями: начатое доводится до
            # конца — оно уже отправлено, и бросить ответ значило бы спросить
            # то же самое ещё раз, — а новых не будет (FR-044).
            if source_id in collected:
                # Объявлено в начале сессии — здесь только исход прогона.
                result.outcomes.append(_already_collected(source_id))
                continue

            if should_stop is not None and should_stop():
                logger.info("сбор сессии %s прерван по команде", session_date)
                # План сессии не доработан, и молчать об этом нельзя: без
                # отметки сессия с собранными котировками и неспрошенными
                # агрегатами объявлялась собранной (FR-058).
                outcome = _not_asked(source_id)
                result.outcomes.append(outcome)
                await _record(repository, run_id, outcome, session_date)
                await session.commit()
                return result

            outcome = await run_source(
                repository, run_id, source_id, session_date, action, on_source=on_source
            )
            result.outcomes.append(outcome)
            await session.commit()

        # Справочники текущего состояния — по суточному гейту, а не на каждую
        # сессию. Оси сессий у них нет: ответ один и тот же, каким бы днём его
        # ни спросили, и ручной догон их поэтому не запрашивает вовсе. При
        # отставании в восемьдесят сессий прежний порядок платил за них сто
        # шестьдесят обращений, переписывая одни и те же строки (FR-055).
        for source_id, reference_action in (
            (
                reference.SECTORS_SOURCE_ID,
                lambda: reference.sync_sectors(iss, repository, session_date),
            ),
            (
                securities.SOURCE_ID,
                lambda: securities.sync_lot_sizes(iss, repository),
            ),
        ):
            if not await reference_is_due(repository, source_id, session_date):
                # Объявлено в начале сессии: в план этого прогона источник не
                # попадает (FR-007, FR-058l).
                continue
            if should_stop is not None and should_stop():
                outcome = _not_asked(source_id)
                result.outcomes.append(outcome)
                await _record(repository, run_id, outcome, session_date)
                await session.commit()
                return result
            outcome = await run_source(
                repository,
                run_id,
                source_id,
                session_date,
                reference_action,
                on_source=on_source,
            )
            result.outcomes.append(outcome)
            await session.commit()

        if positions.SOURCE_ID in collected:
            result.outcomes.append(_already_collected(positions.SOURCE_ID))
            return result

        if should_stop is not None and should_stop():
            logger.info("сбор сессии %s прерван по команде", session_date)
            outcome = _not_asked(positions.SOURCE_ID)
            result.outcomes.append(outcome)
            await _record(repository, run_id, outcome, session_date)
            await session.commit()
            return result

        # Связи инструментов — перед позициями: иначе появление нового фьючерса
        # заметили бы только через сутки, а позиции спрашивались бы вчерашним
        # контрактом.
        #
        # Но не на каждую сессию: список серий описывает СЕГОДНЯШНИЙ состав
        # рынка. Ежедневный цикл идёт от свежих сессий к старым (FR-045), и для
        # всех, кроме первой, сверка заведомо ничего не откроет — запрет
        # датировать задним числом её и отвергнет, — а стоит она трёх обращений
        # к бирже на сессию (FR-049).
        #
        # Датируется ДНЁМ ОБРАЩЕНИЯ, а не датой собираемой сессии: добор
        # пропуска за 14 сентября при календаре до 18-го записывал сегодняшний
        # контракт действующим с 14-го. Правило чинили в ручном догоне, а
        # посессионный путь оставили на дате сессии (FR-049).
        asked_on = await calendar.latest_session(moscow_today()) or session_date
        confirmed = await repository.latest_link_start()
        # `<=`, а не `<`: связи, подтверждённые ЭТИМ ЖЕ днём, пересматривать
        # нечем — список серий за день не меняется, а стоит он трёх обращений
        # к бирже на каждую собираемую сессию (FR-049).
        if confirmed is not None and asked_on <= confirmed:
            logger.debug(
                "сессия %s: связи не пересматриваются, они подтверждены по %s",
                session_date,
                confirmed,
            )
        else:
            await sync_instrument_links(
                repository,
                iss,
                asked_on,
                alias_events=alias_events,
                # Состав доски — из собираемой сессии: её котировки записаны
                # шагом выше, и свежее данных у нас нет.
                traded_on=session_date,
            )
        await session.commit()

        # Задержанный источник — отдельно и с повторами. Он единственный ходит
        # не в биржевой интерфейс данных, а формой на сайт биржи, поэтому у
        # него свой клиент и своё соответствие акций контрактам.
        day = session_date
        delayed = await _run_delayed_source(
            repository,
            run_id,
            positions.SOURCE_ID,
            session_date,
            lambda: _sync_positions(
                settings,
                iss,
                repository,
                day,
                pos_client,
                sessions=None,
                should_stop=should_stop,
                on_source=on_source,
            ),
            on_source=on_source,
        )
        result.outcomes.append(delayed)
        await session.commit()

        return result
    finally:
        if owns_positions:
            await pos_client.__aexit__(None, None, None)
        if owns_client:
            await iss.__aexit__(None, None, None)


async def catch_up(
    session: AsyncSession,
    settings: Settings,
    asof_date: dt.date,
    client: IssClient | None = None,
    cbr_client: httpx.AsyncClient | None = None,
    *,
    positions_client: PositionsClient | None = None,
    sessions: list[dt.date] | None = None,
    source_ids: frozenset[str] | None = None,
    on_session_start: Callable[[dt.date], None] | None = None,
    on_session_done: Callable[[dt.date, bool | str], None] | None = None,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
    on_skip: Callable[[dt.date, str, str | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    run_id: str | None = None,
    prepare_assets: bool = True,
) -> CatchupResult:
    """Догнать пропущенные сессии окна.

    Четыре свойства, ради которых написано именно так:

    - **диапазонные источники идут ПЕРВЫМИ.** Раньше они шли после цикла по
      датам, и прогон, остановленный на 224-й сессии из 309, не собрал их
      вовсе: индексы имели по 1 сессии, USD и ставка — по 2. Границы дыры
      известны до начала цикла, обращений там единицы, и после них прерывание
      уже ничего не теряет;
    - **сессии идут от старых к новым.** При прерывании остаётся закрытым более
      ранний участок окна, а не разрозненные даты;
    - **недоступность одной сессии не отменяет остальные.** Задержанные
      модальности за старые даты могут быть недоступны по своей природе;
    - **порога по числу сессий нет.** Догоняется всё окно: система работает с
      314 сессиями за раз, и остановка на десятом дне сделала бы механизм
      бесполезным ровно в том случае, ради которого он заведён.

    Остановка мягкая: ``should_stop`` проверяется МЕЖДУ сессиями. День,
    собранный наполовину, неотличим от собранного полностью.
    """
    result = CatchupResult()

    if not settings.market_data_catchup_enabled:
        result.skipped_reason = "догон выключен настройкой"
        return result

    if sessions is None:
        report = await gaps.find_gaps(session, settings, asof_date)

        if report.needs_backfill:
            # Разграничение по состоянию хранилища, а не по числу пропущенных
            # дней: на чистой базе календарь уже полон, и «пропущено 314
            # сессий» — нормальное состояние новой установки, а не авария.
            result.needs_backfill = True
            result.skipped_reason = "в хранилище нет наблюдений: нужна первичная загрузка"
            logger.warning("догон не выполняется: %s", result.skipped_reason)
            return result

        result.requested = list(report.missing_sessions)
    else:
        result.requested = list(sessions)

    # Незакрытая сессия не собирается и по команде человека (FR-029b). Правило
    # жило только в автоматическом пути, и кнопка могла забрать сегодняшний день
    # посреди торгов: дневные бары внутри сессии ещё меняются, а незавершённая
    # сессия в признаках модели — утечка будущего. Диапазон при этом не
    # отвергается целиком: собирается всё закрытое, а сегодняшнее ждёт вечера.
    from financial_ai.market_data.advance import session_is_closed

    withheld = [day for day in result.requested if not session_is_closed(day, settings)]
    if withheld:
        logger.info(
            "догон: сессий отложено до закрытия — %s",
            ", ".join(str(day) for day in withheld),
        )
        # Причина отложения доходит до человека, а не остаётся в логе: без неё
        # сессия просто исчезает из плана и выглядит потерянной (FR-002).
        if on_skip is not None:
            for day in withheld:
                on_skip(day, "withheld_until_close", "сессия ещё не закрылась")
        result.requested = [day for day in result.requested if day not in set(withheld)]

    if not result.requested:
        return result

    logger.info("догон: к сбору сессий %d", len(result.requested))

    # ОДИН идентификатор на весь прогон, сколько бы сессий тот ни охватил.
    # Журнал группирует исходы по нему и считает в прогоне сессии; при
    # идентификаторе на сессию догон из восьмидесяти двух сессий показывался
    # восемьюдесятью двумя прогонами по одному дню, а список последних прогонов
    # вмещал пять последних дней вместо пяти последних прогонов (FR-052).
    #
    # Продолжение передаёт идентификатор остановленного прогона: оно
    # продолжает ЕГО, а значит и собранное им переспрашивать не должно
    # (FR-058b, FR-058e).
    run_id = run_id or str(uuid.uuid4())

    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)
    owns_client = client is None
    iss = client or IssClient(build_iss_config(settings))
    if owns_client:
        await iss.__aenter__()

    # Повторы клиент ведёт сам, и без этого признака остановка ждала их все
    # (FR-058j).
    if hasattr(iss, "should_stop"):
        iss.should_stop = should_stop

    # Один клиент позиций на весь прогон: в нём живут темп обращений и уже
    # найденные первые доступные даты. Новый клиент на каждую сессию искал бы
    # их заново — это и есть «обращения, заведомо не приносящие данных».
    owns_positions = positions_client is None
    pos_client = positions_client or PositionsClient(settings)
    if owns_positions:
        await pos_client.__aenter__()

    # Недоступный источник перестаёт опрашиваться в пределах прогона: при
    # длинной дыре он иначе стоит по семь обращений на каждую сессию.
    health = _SourceHealth(settings.market_data_source_failure_streak)

    try:
        # ПЕРВЫМИ — диапазонные источники: одно обращение на ряд независимо от
        # длины дыры. После них прерывание уже ничего не теряет.
        await _catch_up_ranges(
            repository,
            run_id,
            iss,
            result.requested[0],
            result.requested[-1],
            cbr_client,
            source_ids,
            on_source=on_source,
            should_stop=should_stop,
        )

        if should_stop is not None and should_stop():
            logger.info("догон остановлен до опознания бумаг")
            return result

        # Опознание бумаг — ДО сессий и НЕЗАВИСИМО от выбора источников.
        # Прежде оно шло довеском к сверке связей, а та выполняется только
        # когда выбраны позиции: ручной сбор одних котировок заводил
        # переименованной бумаге вторую сущность, не спросив ISIN ни разу
        # (FR-048).
        #
        # Датируется НАЧАЛОМ окна: действие имени обязано покрывать каждую
        # сессию, наблюдения за которую прогон собирается записать. Имя — не
        # связь: связь со временем меняется по существу, а имя лишь указывает
        # на сущность, и два имени одной сущности не могут значить разные
        # бумаги в разные дни одного окна. Датированное концом окна, опознание
        # рвало ряд внутри ОДНОГО прогона (FR-048).
        alias_events = []
        if prepare_assets:
            alias_events = await _sync_aliases(repository, iss, result.requested[0])

        # Связи — тоже раз на прогон, и по той же причине, что диапазонные
        # источники: ответ один на всё окно. Но датируются ДНЁМ ОБРАЩЕНИЯ, а не
        # датой из окна: окно говорит о том, что собирают, а источник — о
        # сегодня. Догон до 10 сентября принимал сегодняшний контракт как
        # действующий с 10-го, хотя прежняя связь шла с 1-го (FR-049).
        # Проверка перед сверкой связей: на изменившемся составе это десятки
        # обращений, и нажатие в эти секунды не делало ничего — остановка
        # выглядела зависшей (FR-058h).
        if should_stop is not None and should_stop():
            logger.info("догон остановлен до сверки связей")
            await session.commit()
            return result

        if prepare_assets and (source_ids is None or positions.SOURCE_ID in source_ids):
            asked_on = await calendar.latest_session(moscow_today()) or result.requested[-1]
            confirmed = await repository.latest_link_start()
            if confirmed is not None and asked_on < confirmed:
                logger.info("догон: связи не пересматриваются, они подтверждены по %s", confirmed)
            else:
                await sync_instrument_links(
                    repository,
                    iss,
                    asked_on,
                    alias_events=alias_events,
                    traded_on=await repository.latest_observed_session(),
                )

        await session.commit()

        for day in result.requested:
            # Проверка МЕЖДУ сессиями: начатую доводим до конца.
            if should_stop is not None and should_stop():
                logger.info("догон остановлен перед сессией %s", day)
                break

            if on_session_start is not None:
                on_session_start(day)

            outcomes = await _catch_up_session(
                repository,
                run_id,
                iss,
                day,
                source_ids,
                settings=settings,
                positions_client=pos_client,
                sessions=result.requested,
                health=health,
                on_source=on_source,
                should_stop=should_stop,
            )
            await session.commit()

            # Три исхода сессии, а не два, и различие здесь существенное.
            #
            # **Прерванная** — та, чей план не доработан по команде человека.
            # Прежде судили по одним котировкам, и остановка после них, но до
            # агрегатов, оставляла сессию с исходом «собрана»: продолжение
            # брало следующую, а недобранные агрегаты не добирало никогда
            # (FR-058).
            #
            # **Незакрытая** — та, где упали котировки: на них держится
            # пространство строк. Недоступность задержанной модальности за
            # старую дату незакрытостью не является — это нормальное явление,
            # и объявлять её работой значило бы перевыбирать такой день вечно.
            interrupted = any(outcome.status == STATUS_STOPPED for outcome in outcomes)
            quotes = next((o for o in outcomes if o.source_id == equity_d1.SOURCE_ID), None)
            closed = quotes is None or quotes.status not in _UNFINISHED

            if interrupted:
                result.interrupted.append(day)
            elif closed:
                result.closed.append(day)
            else:
                result.failed.append(day)

            if on_session_done is not None:
                on_session_done(day, INTERRUPTED if interrupted else closed)
    finally:
        if owns_positions:
            await pos_client.__aexit__(None, None, None)
        if owns_client:
            await iss.__aexit__(None, None, None)

    logger.info("догон: закрыто %d из %d", len(result.closed), len(result.requested))
    return result


async def _catch_up_session(
    repository: MarketDataRepository,
    run_id: str,
    iss: IssClient,
    session_date: dt.date,
    source_ids: frozenset[str] | None = None,
    *,
    settings: Settings,
    positions_client: PositionsClient,
    sessions: list[dt.date],
    health: _SourceHealth,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[SourceOutcome]:
    """Собрать одну пропущенную сессию источниками с выборкой по дате.

    Справочник секторов сюда не входит: он отражает **текущую** принадлежность,
    истории у него нет, и догонять там нечего. Дивиденды тоже: они собираются по
    активу за всю историю сразу, поэтому ежедневный прогон уже покрывает
    пропущенные дни.
    """
    outcomes: list[SourceOutcome] = []
    # То же правило, что и в ежедневном пути: собранное за эту сессию заново не
    # спрашивается (FR-058e).
    collected = await completeness.closed_sources_for(repository, session_date)

    # Известное объявляется сразу — см. ежедневный путь (FR-058l). Только по
    # тем, кого собирает сама сессия: диапазонный источник идёт раз на прогон
    # и свой исход уже объявил.
    if on_source is not None:
        selected = _CATCHUP_SESSION_SOURCES if source_ids is None else source_ids
        for source_id in sorted(collected & _CATCHUP_SESSION_SOURCES & selected):
            done = _already_collected(source_id)
            on_source(source_id, done.status, done)

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
            lambda: _sync_positions(
                settings,
                iss,
                repository,
                session_date,
                positions_client,
                sessions,
                should_stop=should_stop,
                on_source=on_source,
            ),
        ),
    ):
        # Остановка относится только к выбранной и ещё не выполненной работе.
        if source_ids is not None and source_id not in source_ids:
            continue
        if source_id in collected:
            outcomes.append(_already_collected(source_id))
            continue
        if not health.is_open(source_id):
            outcome = SourceOutcome(
                source_id,
                STATUS_FAILED,
                failure_reason="источник недоступен после серии неудач",
            )
            outcomes.append(outcome)
            await _record(repository, run_id, outcome, session_date, trigger=TRIGGER_CATCHUP)
            continue
        # Остановка проверяется и здесь, между источниками. Прежде она ждала
        # конца сессии, а сессия с позициями идёт по обращению на каждый из
        # десятков контрактов — минуты после нажатия. Недобранные источники
        # остаются в плане: полнота считается по каждому из них (FR-044).
        if should_stop is not None and should_stop():
            logger.info("догон остановлен внутри сессии %s", session_date)
            # План сессии не доработан. Без этой отметки сессия с собранными
            # котировками и неспрошенными агрегатами объявлялась собранной, и
            # продолжение её больше не брало (FR-058).
            #
            # Отметка идёт и в ЖУРНАЛ: состояние исчезает вместе с процессом, а
            # журнал — нет, и пока исход «не спрошен» жил только в памяти,
            # остановленный прогон и в журнале выглядел завершённым (FR-050).
            outcome = _not_asked(source_id)
            outcomes.append(outcome)
            await _record(repository, run_id, outcome, session_date, trigger=TRIGGER_CATCHUP)
            break

        outcome = await run_source(
            repository,
            run_id,
            source_id,
            session_date,
            action,
            trigger=TRIGGER_CATCHUP,
            on_source=on_source,
        )
        if outcome.status == STATUS_OK:
            health.record(source_id, True)
        elif outcome.counts_as_unavailable:
            health.record(source_id, False)
        outcomes.append(outcome)

    return outcomes


async def _catch_up_ranges(
    repository: MarketDataRepository,
    run_id: str,
    iss: IssClient,
    date_from: dt.date,
    date_till: dt.date,
    cbr_client: httpx.AsyncClient | None,
    source_ids: frozenset[str] | None = None,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Закрыть дыру источниками, умеющими выборку за период.

    Число обращений здесь не зависит от длины дыры. Прогон записывается на
    дату конца периода: он относится ко всему промежутку, а не к одной сессии.

    Идентификатор прогона — общий с посессионной частью: это один прогон, а не
    два соседних (FR-052).
    """
    window = await repository.sessions_between(date_from, date_till)
    group = next(group for group in groups.GROUPS if global_series.SOURCE_ID in group.source_ids)
    for source_id, action in (
        (
            global_series.SOURCE_ID,
            lambda: global_series.sync_iss_series_range(
                iss, repository, date_from, date_till, required_dates=tuple(window)
            ),
        ),
        (
            cbr.SOURCE_ID,
            lambda: _sync_cbr_range(
                repository,
                date_from,
                date_till,
                cbr_client,
                should_stop,
                required_dates=tuple(window),
            ),
        ),
    ):
        if source_ids is not None and source_id not in source_ids:
            continue
        closed = await completeness.closed_sessions(repository, group, source_id, window)
        if window and set(window) <= closed:
            if on_source is not None:
                done = _already_collected(source_id)
                on_source(source_id, done.status, done)
            continue
        if should_stop is not None and should_stop():
            break
        await run_source(
            repository,
            run_id,
            source_id,
            date_till,
            action,
            trigger=TRIGGER_CATCHUP,
            period=(date_from, date_till),
            on_source=on_source,
        )
        # Остановка может завершить догон сразу после диапазонной части.
        await repository.commit()


async def ingest_and_rank(
    session: AsyncSession,
    settings: Settings,
    session_date: dt.date | None = None,
    client: IssClient | None = None,
    cbr_client: httpx.AsyncClient | None = None,
) -> tuple[IngestResult, object | None]:
    """Собрать данные сессии, материализовать набор и запросить ранжирование.

    Сбой ранжирования **не отменяет** собранные данные: они уже сохранены, и
    повторить можно только запрос. Поэтому исход ранжирования возвращается
    отдельно от исхода сбора.
    """
    # Импорт здесь, а не в шапке: сбор данных не должен зависеть от звена
    # ранжирования — оно может отсутствовать, и это не мешает собирать.
    from financial_ai.daily_ml import readiness
    from financial_ai.ranking import client as ranking_client
    from financial_ai.ranking import dataset as dataset_module

    result = await ingest_session(session, settings, session_date, client, cbr_client)
    if result.session_date is None or not result.succeeded:
        logger.info("ранжирование пропущено: сбор не завершён успешно")
        return result, None

    # Догон здесь НЕ выполняется: он стартует только по команде человека.
    # Неуправляемый догон уходил на сотни обращений к бирже без спроса и без
    # возможности вмешаться — один прогон на живых данных это показал.
    try:
        dataset = await dataset_module.build_dataset(session, settings, result.session_date)
        if not readiness.dataset_is_complete(dataset.incomplete, settings):
            logger.warning("ранжирование пропущено: обязательный вход неполон")
            return result, None
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
    should_stop: Callable[[], bool] | None = None,
) -> VerificationResult:
    """Дневные макроряды ЦБ за сессию.

    Режим доступности у них иной: по time_semantics.md они публикуются ДО
    закрытия сессии и уже относятся к дню `t`. Поэтому запрашиваются за ту же
    дату, а не за предыдущую.
    """
    return await _sync_cbr_range(
        repository,
        session_date,
        session_date,
        client,
        should_stop,
        required_dates=(session_date,),
    )


async def _sync_cbr_range(
    repository: MarketDataRepository,
    date_from: dt.date,
    date_till: dt.date,
    client: httpx.AsyncClient | None = None,
    should_stop: Callable[[], bool] | None = None,
    required_dates: tuple[dt.date, ...] | None = None,
) -> VerificationResult:
    """Макроряды ЦБ за период.

    Страницы Банка России уже принимают границы периода, поэтому дыра любой
    длины закрывается двумя обращениями — по одному на ключевую ставку и ЗКЦ.

    **Ставка и кривая независимы, и падение одной не отменяет другую.** Прежде
    ошибка кривой уносила с собой весь источник, а полученная ставка не
    засчитывалась ничем: на стенде 2026-09-20 у `CBR_KEY_RATE` оказалось 311
    значений против 308 у каждой точки `CBR_ZCYC_*`. Теперь получившаяся часть
    сохраняется, а незавершённая называется в исходе и остаётся работой —
    источник не закрывается (FR-032).
    """
    config = cbr.CbrConfig()
    written = 0
    unfinished: list[str] = []
    evidence: list[WorkEvidence] = []
    required = set(required_dates or ((date_from,) if date_from == date_till else ()))

    try:
        key_rate = await cbr.fetch_key_rate(config, date_from, date_till, client, should_stop)
    except SourceStoppedError as error:
        raise SourceStoppedError(written + error.rows_written) from error
    except Exception as error:  # noqa: BLE001 — кривая не зависит от ставки
        logger.warning("ЦБ: ключевая ставка не собрана: %s", error)
        unfinished.append(cbr.KEY_RATE_SERIES_ID)
    else:
        written += await repository.upsert_global_values(cbr.KEY_RATE_SERIES_ID, key_rate)
        proved_dates = set(key_rate) & required if required else set(key_rate)
        evidence.extend(WorkEvidence(day, cbr.KEY_RATE_SERIES_ID) for day in sorted(proved_dates))
        if required and not key_rate:
            evidence.extend(
                WorkEvidence(
                    day,
                    cbr.KEY_RATE_SERIES_ID,
                    result_kind="confirmed_absence",
                    reason_code="verified_empty_cbr_table",
                )
                for day in sorted(required)
            )

    if should_stop is not None and should_stop():
        raise SourceStoppedError(written)

    try:
        zcyc = await cbr.fetch_zcyc(config, date_from, date_till, client, should_stop)
    except SourceStoppedError as error:
        raise SourceStoppedError(written + error.rows_written) from error
    except Exception as error:  # noqa: BLE001 — ставка уже сохранена и не теряется
        logger.warning("ЦБ: кривая бескупонной доходности не собрана: %s", error)
        unfinished.append("кривая ЗКЦ")
    else:
        for series_id, values in zcyc.items():
            written += await repository.upsert_global_values(series_id, values)
        required_series = {f"{cbr.ZCYC_SERIES_PREFIX}{term}" for term in cbr.REQUIRED_ZCYC_TERMS}
        curve_dates = (
            set.intersection(*(set(zcyc.get(series_id, {})) for series_id in required_series))
            if required_series
            else set()
        )
        curve_dates = curve_dates & required if required else curve_dates
        evidence.extend(WorkEvidence(day, "CBR_ZCYC_CURVE") for day in sorted(curve_dates))

    proved = {(item.session_date, item.work_key) for item in evidence}
    missing_dates = [
        f"{work_key}:{day.isoformat()}"
        for work_key in (cbr.KEY_RATE_SERIES_ID, "CBR_ZCYC_CURVE")
        for day in sorted(required)
        if (day, work_key) not in proved
    ]
    missing = [*unfinished, *missing_dates]
    return VerificationResult(
        rows_written=written,
        evidence=tuple(evidence),
        complete=not missing,
        detail=f"не собрано: {', '.join(missing)}" if missing else None,
    )


async def _run_delayed_source(
    repository: MarketDataRepository,
    run_id: str,
    source_id: str,
    session_date: dt.date,
    action: object,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
) -> SourceOutcome:
    """Выполнить сбор задержанного источника с повторами.

    Данные приходят позже закрытия сессии. Если после всех попыток их всё ещё
    нет — исход `failed`, и наблюдение просто отсутствует. Записать его датой
    следующей сессии НЕЛЬЗЯ: это исказило бы историю необратимо и незаметно.
    """
    outcome = SourceOutcome(source_id, STATUS_FAILED, failure_reason="не выполнялся")
    for attempt in range(1, DELAYED_SOURCE_ATTEMPTS + 1):
        outcome = await run_source(
            repository, run_id, source_id, session_date, action, on_source=on_source
        )
        if outcome.status == STATUS_OK:
            return outcome
        if outcome.status == STATUS_STOPPED:
            # Повторы заведены на случай «данные ещё не опубликованы».
            # Остановка к этому случаю не относится, а умножала ожидание
            # человека на число попыток (FR-058d).
            return outcome
        logger.info(
            "задержанный источник %s: попытка %d из %d не дала данных",
            source_id,
            attempt,
            DELAYED_SOURCE_ATTEMPTS,
        )
        if attempt < DELAYED_SOURCE_ATTEMPTS and on_source is not None:
            # Номер попытки виден на экране: без него ожидание публикации
            # неотличимо от зависания, а повтор — от бездействия.
            on_source(
                source_id,
                "running",
                SourceOutcome(
                    source_id,
                    STATUS_FAILED,
                    failure_reason=(
                        f"данные ещё не опубликованы, попытка {attempt + 1} "
                        f"из {DELAYED_SOURCE_ATTEMPTS}"
                    ),
                ),
            )
    return outcome


# Источники, которые собирает САМА сессия. Диапазонные и суточные идут раз на
# прогон, и объявлять про них что-либо от имени сессии нельзя: их исход уже
# назван, и «собран ранее» затёрло бы его (FR-058l).
_DAILY_SESSION_SOURCES = frozenset(
    spec.source_id for spec in plan.for_mode(plan.MODE_DAILY) if spec.scope == plan.SESSION
)
_CATCHUP_SESSION_SOURCES = frozenset(
    spec.source_id for spec in plan.for_mode(plan.MODE_MANUAL) if spec.scope == plan.SESSION
)


def _already_collected(source_id: str) -> SourceOutcome:
    """Исход источника, собранного за эту сессию раньше.

    Показывается СОБРАННЫМ, а не пропущенным: за эту сессию он действительно
    собран, и счёт источников сессии обязан это учесть (FR-058e).
    """
    return SourceOutcome(source_id, STATUS_OK, detail="собран ранее")


def _not_asked(source_id: str) -> SourceOutcome:
    """Исход источника, до которого прогон не дошёл из-за остановки.

    Не «ок» и не «не удался»: его не спрашивали. Но и молчания быть не может —
    без записи сессия выглядела бы собранной по тем источникам, что успели
    пройти (FR-050, FR-058).
    """
    return SourceOutcome(source_id, STATUS_STOPPED, failure_reason="не спрошен")


async def run_source(
    repository: MarketDataRepository,
    run_id: str,
    source_id: str,
    session_date: dt.date | None,
    action: object,
    trigger: str = TRIGGER_DAILY,
    period: tuple[dt.date, dt.date] | None = None,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
) -> SourceOutcome:
    """Выполнить сбор одного источника, зафиксировав исход.

    Неуспех одного источника не отменяет успех остальных и не затрагивает
    ранее собранные данные: исключение ловится здесь и записывается.

    ``period`` — отрезок, который исход покрывает. У посессионного источника он
    равен сессии и подставляется сам; источник с выборкой за диапазон передаёт
    его явно, иначе выглядел бы несобравшим всё, кроме последней сессии.

    ``on_source`` зовётся дважды: перед обращением и после него. Без этого
    человек видит «идёт сбор» и не видит, чем система занята прямо сейчас, —
    долгий источник неотличим от зависания.

    **Исход заводится ДО обращения**, без отметки завершения, и дополняется
    после. Отметка о прерванном прогоне ставится записям без отметки завершения
    (FR-041), а обращение, оборванное вместе с процессом, не оставляло записи
    вовсе: ровно тот случай, ради которого отметка существует, ею и не
    покрывался (FR-052).
    """
    if on_source is not None:
        on_source(source_id, "running", None)

    started = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id=run_id,
        source_id=source_id,
        status="running",
        started_at=started,
        finished_at=None,
        session_date=session_date,
        trigger=trigger,
        period_from=period[0] if period else None,
        period_till=period[1] if period else None,
    )
    await repository.commit()

    verification: object | None = None
    try:
        verification = await action()  # type: ignore[operator]
    except SourceStoppedError as stop:
        # Собранное уже записано источником: остановка не отменяет запись, она
        # отменяет утверждение «этот день по источнику собран» (FR-050).
        for evidence in stop.evidence:
            await repository.record_work_evidence(
                source_id=source_id,
                session_date=evidence.session_date,
                work_key=evidence.work_key,
                result_kind=evidence.result_kind,
                reason_code=evidence.reason_code,
                origin_run_id=run_id,
            )
        outcome = SourceOutcome(
            source_id,
            STATUS_STOPPED,
            rows_written=stop.rows_written,
            failure_reason=stop.detail,
            counts_as_unavailable=False,
        )
        logger.info("сбор: источник %s прерван: %s", source_id, stop.detail)
    except SourcePartialError as partial:
        # Полученное уже записано источником и остаётся в хранилище. Неуспехом
        # исход называется не поэтому, а потому что применимая работа сделана
        # не вся: один собранный ряд из пяти не говорит ничего про остальные
        # четыре, и «ок» здесь закрыл бы сессию всем пяти (FR-032).
        for evidence in partial.evidence:
            await repository.record_work_evidence(
                source_id=source_id,
                session_date=evidence.session_date,
                work_key=evidence.work_key,
                result_kind=evidence.result_kind,
                reason_code=evidence.reason_code,
                origin_run_id=run_id,
            )
        outcome = SourceOutcome(
            source_id,
            STATUS_FAILED,
            rows_written=partial.rows_written,
            failure_reason=partial.detail,
            counts_as_unavailable=partial.counts_as_unavailable,
        )
        logger.warning(
            "сбор: источник %s отработал не всю работу (%s), сохранено строк: %d",
            source_id,
            partial.detail,
            partial.rows_written,
        )
    except IssError as error:
        outcome = SourceOutcome(
            source_id, STATUS_FAILED, failure_reason=str(error), counts_as_unavailable=True
        )
        logger.warning("сбор: источник %s не удался: %s", source_id, error)
    except Exception as error:
        outcome = SourceOutcome(
            source_id, STATUS_FAILED, failure_reason=repr(error), counts_as_unavailable=True
        )
        logger.exception("сбор: источник %s завершился ошибкой", source_id)
    else:
        if isinstance(verification, VerificationResult):
            for evidence in verification.evidence:
                await repository.record_work_evidence(
                    source_id=source_id,
                    session_date=evidence.session_date,
                    work_key=evidence.work_key,
                    result_kind=evidence.result_kind,
                    reason_code=evidence.reason_code,
                    origin_run_id=run_id,
                )
            complete = verification.complete
            if complete and session_date is not None and period is None:
                proved_keys = {
                    item.work_key
                    for item in verification.evidence
                    if item.session_date == session_date
                }
                complete = required_work_keys(source_id) <= proved_keys
            outcome = SourceOutcome(
                source_id,
                STATUS_OK if complete else STATUS_FAILED,
                rows_written=verification.rows_written,
                failure_reason=(None if complete else verification.detail or "работа не доказана"),
                detail=(plan.describe(source_id, verification.rows_written) if complete else None),
                counts_as_unavailable=(
                    verification.counts_as_unavailable if not complete else False
                ),
            )
        elif source_id in {
            reference.SECTORS_SOURCE_ID,
            securities.SOURCE_ID,
            trading_calendar.SOURCE_ID,
        }:
            # У суточных справочников нет оси сессий и покрытия окна.
            rows = int(verification)  # type: ignore[arg-type]
            outcome = SourceOutcome(
                source_id,
                STATUS_OK,
                rows_written=rows,
                detail=plan.describe(source_id, rows),
            )
        else:
            outcome = SourceOutcome(
                source_id,
                STATUS_FAILED,
                failure_reason="источник не вернул явный результат проверки",
            )

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
        period_from=period[0] if period else None,
        period_till=period[1] if period else None,
        coverage_version=(
            CURRENT_COVERAGE_VERSION
            if outcome.status == STATUS_OK and isinstance(verification, VerificationResult)
            else None
        ),
        coverage_reason=(
            "verified_work_evidence"
            if outcome.status == STATUS_OK and isinstance(verification, VerificationResult)
            else None
        ),
    )

    if on_source is not None:
        on_source(source_id, outcome.status, outcome)
    return outcome


async def _record(
    repository: MarketDataRepository,
    run_id: str,
    outcome: SourceOutcome,
    session_date: dt.date | None,
    trigger: str = TRIGGER_DAILY,
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
        trigger=trigger,
    )


class _SourceHealth:
    """Учёт подряд идущих неудач источника в пределах одного прогона.

    Прекращать после ПЕРВОЙ неудачи нельзя: единичный сбой сети закрыл бы
    источник на весь прогон, хотя следующая сессия могла бы пройти. Поэтому
    порог, и он задаётся конфигурацией.

    Состояние живёт ровно столько, сколько прогон: следующий начинает с
    чистого листа, потому что недоступность источника — свойство момента.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._failures: dict[str, int] = {}

    def is_open(self, source_id: str) -> bool:
        return self._failures.get(source_id, 0) < self._limit

    def record(self, source_id: str, succeeded: bool) -> None:
        if succeeded:
            self._failures[source_id] = 0
            return
        streak = self._failures.get(source_id, 0) + 1
        self._failures[source_id] = streak
        if streak == self._limit:
            logger.warning(
                "источник %s не отвечает %d сессии подряд: в этом прогоне больше не спрашиваем",
                source_id,
                streak,
            )


async def _sync_aliases(
    repository: MarketDataRepository,
    iss: IssClient,
    session_date: dt.date,
) -> list[links.LinkEvent]:
    """Опознать бумаги по ISIN до записи наблюдений сессии.

    Неудача здесь прогон не отменяет. Список ISIN и котировки берутся у одного
    и того же интерфейса с разницей в секунду: если не отвечает один, не
    ответит и другой, и остановка сбора ничего не спасла бы. А риск от
    продолжения ограничен одной сессией одной бумаги, переименованной ровно в
    этот день, — ровно тем, что до FR-048 случалось при КАЖДОМ переименовании.
    """
    try:
        events = await links.sync_aliases(repository, iss, session_date)
    except IssError as error:
        logger.warning("опознание бумаг за %s не удалось: %s", session_date, error)
        return []

    for event in events:
        logger.info("состав инструментов: %s", event.describe())
    return events


async def reference_is_due(
    repository: MarketDataRepository,
    source_id: str,
    session_date: dt.date,
) -> bool:
    """Пора ли спрашивать справочник текущего состояния.

    Раз в сутки. У отраслевой принадлежности и размеров лотов оси сессий нет:
    ответ описывает СЕГОДНЯШНЕЕ состояние, каким бы днём его ни спросили.
    Спрашивать их на каждую догоняемую сессию значило бы переписывать одни и те
    же строки столько раз, сколько дней в отставании (FR-055).

    Признак берётся из журнала исходов, а не из памяти процесса: перезапуск
    сборщика не должен приводить к повторному обходу справочников.
    """
    last = await repository.last_successful_run_at(source_id)
    if last is None:
        return True
    return last.astimezone(MOSCOW).date() < moscow_today()


async def sync_instrument_links(
    repository: MarketDataRepository,
    iss: IssClient,
    session_date: dt.date,
    alias_events: list[links.LinkEvent] | None = None,
    traded_on: dt.date | None = None,
) -> list[links.LinkEvent]:
    """Привести связи инструментов в соответствие с составом — раз на прогон.

    ``session_date`` — ПОСЛЕДНЯЯ сессия окна прогона, а не каждая его сессия и
    не первая. Список серий отвечает про сегодня, и датировать его началом окна
    значило бы утверждать связь за дни, о которых источник не говорил; при
    смене семейства прежний интервал закрывался бы датой раньше собственного
    начала (FR-049, FR-034).
    """
    events = await links.sync_links(
        repository, iss, session_date, alias_events=alias_events, traded_on=traded_on
    )
    for event in events:
        logger.info("состав инструментов: %s", event.describe())
    return events


async def _sync_positions(
    settings: Settings,
    iss: IssClient,
    repository: MarketDataRepository,
    session_date: dt.date,
    client: PositionsClient | None,
    sessions: list[dt.date] | None,
    should_stop: Callable[[], bool] | None = None,
    on_source: Callable[[str, str, SourceOutcome | None], None] | None = None,
) -> VerificationResult:
    """Позиции по фьючерсам за одну сессию.

    Чем спрашивать — знает связь бумаги, приведённая в соответствие с составом
    инструментов один раз на прогон (:func:`sync_instrument_links`). Здесь её
    больше не трогают: список серий описывает СЕГОДНЯШНИЙ состав рынка, и
    спрашивать его заново на каждую сессию догона — три обращения к бирже за
    ответом, который не изменится, помноженные на длину дыры.
    """
    if client is None:
        raise IssError("клиент источника позиций не настроен")

    # Признак остановки уходит и в клиент: повторы обращения он ведёт сам, и
    # без этого «идущий источник доводится до конца» означало не отправленный
    # запрос, а серию из четырёх с нарастающей паузой (FR-058j).
    if hasattr(client, "should_stop"):
        client.should_stop = should_stop

    unit = plan.UNITS[positions.SOURCE_ID]

    def progress(done: int, total: int) -> None:
        # Ход по инструментам виден на экране: источник идёт минутами, и одно
        # слово «идёт» всё это время неотличимо от зависания (FR-058i).
        if on_source is not None:
            on_source(
                positions.SOURCE_ID,
                "running",
                SourceOutcome(
                    positions.SOURCE_ID,
                    STATUS_OK,
                    detail=f"{done} из {total} {plan.plural(total, *unit)}",
                ),
            )

    return await positions.sync_positions(
        client,
        repository,
        session_date,
        sessions=sessions,
        should_stop=should_stop,
        on_progress=progress,
    )
