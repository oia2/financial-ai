"""Доведение данных до последней ЗАКРЫТОЙ торговой сессии.

Недостающее звено между ежедневным сбором и догоном.

Календарь торговых сессий расширяет только ежедневный цикл, а догон ищет
пропуски **внутри** окна, оканчивающегося последней известной сессией — то есть
о новых сессиях он не знает вовсе. После простоя система честно, но бесполезно
стоит на старой границе: на стенде это выглядело как «последняя сессия 03.09»
при сегодняшнем 12.09.

Два правила, и оба не обсуждаются:

- **незавершённая сессия не собирается.** Дневные бары внутри сессии ещё
  меняются, а незакрытая сессия в признаках модели — утечка будущего;
- **разрыв догоняется на всю глубину окна догона.** Сессия старше окна в набор
  не попадёт, и догонять её незачем — эта граница и служит пределом. Плата
  принята осознанно: после долгого простоя восстановление обращается к бирже
  столько раз, сколько сессий пропущено. Предел можно вернуть одним числом в
  конфигурации, и ради этого поле сохранено: неуправляемый догон однажды ушёл
  на 2909 обращений без спроса, и рычаг против этого должен остаться.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, ingest
from financial_ai.market_data.calendar import MOSCOW, TradingCalendar, moscow_now
from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import equity_d1, trading_calendar

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    """Что удалось довести и что осталось."""

    last_closed_session: dt.date | None = None
    collected: list[dt.date] = field(default_factory=list)
    pending: list[dt.date] = field(default_factory=list)
    gap_sessions: int = 0
    limit_exceeded: bool = False

    @property
    def moved(self) -> bool:
        return bool(self.collected)


def session_is_closed(
    session_date: dt.date, settings: Settings, now: dt.datetime | None = None
) -> bool:
    """Завершилась ли сессия к текущему моменту.

    Сегодняшняя сессия считается закрытой только после настроенного времени
    сбора. До него собирать её нельзя: модель наблюдает ЗАВЕРШЁННУЮ сессию.
    """
    current = now or moscow_now()
    if session_date < current.date():
        return True
    if session_date > current.date():
        return False

    return current.time() >= _threshold_time(settings)


async def pending_sessions(
    session: AsyncSession, settings: Settings, now: dt.datetime | None = None
) -> tuple[list[dt.date], dt.date | None]:
    """Закрытые сессии окна без собранных котировок, и последняя закрытая.

    **Ищется нехватка во всём окне, а не отставание границы.** Прежде список
    считался как «дни после последнего собранного», и пропуск ВНУТРИ окна в него
    не попадал вовсе: собрано 04.09, дыра 05.09, собрано 08.09 — граница стоит на
    08.09, и 05.09 не догонялся никогда. Ранжирование при этом не запускалось,
    потому что полнота требуется по всему окну, а причина не была видна нигде
    (FR-029a).

    Признак собранности — успешный сбор котировок: они задают пространство строк,
    и без них сессии в наборе нет. Успех при нуле наблюдений тоже считается
    собранным — биржа ответила, данных за день нет.

    Сессия, которую недавно уже пытались собрать, пропускается: см.
    `market_data_retry_after_minutes`.
    """
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    moment = now or moscow_now()
    today = moment.date()
    known = await calendar.window(
        await repository.latest_trading_session(today) or today,
        settings.catchup_window_sessions,
    )
    if not known:
        return [], None

    closed = [day for day in known if session_is_closed(day, settings, moment)]
    if not closed:
        return [], None

    last_closed = closed[-1]

    missing = await completeness.incomplete_sessions(
        repository, calendar, settings, last_closed, closed=closed
    )
    if not missing:
        return [], last_closed

    # Предел попыток. Источник, недоступный за конкретную дату по своей природе,
    # иначе перевыбирался бы вечно: сессия остаётся неполной, значит остаётся в
    # списке, значит собирается снова — и так каждые пятнадцать минут без конца.
    attempts = await repository.attempts_by_session(missing)
    within_limit = await within_attempt_limit(repository, missing, settings)
    for day in missing:
        if day not in within_limit:
            logger.warning(
                "сессия %s не закрылась за %d попыток: нужен управляемый догон",
                day,
                attempts.get(day, 0),
            )
            await repository.record_skip(
                session_date=day,
                reason="attempts_exhausted",
                decided_at=dt.datetime.now(dt.UTC),
                detail=(
                    f"{attempts.get(day, 0)} попыток из {settings.market_data_session_max_attempts}"
                ),
            )

    # Отметка времени берётся по котировкам: `ingest_session` гонит все источники
    # за дату одним заходом и котировки — первыми, поэтому их последняя попытка и
    # есть «когда мы в последний раз брались за этот день», какой бы источник ни
    # оставался незакрытым.
    ready = await selectable(repository, within_limit, settings, moment)

    # Сессия, отложенная выдержкой, не исчезает молча: человек видит, что она
    # ждёт повтора, и через сколько (FR-002).
    for day in within_limit:
        if day not in ready:
            await repository.record_skip(
                session_date=day,
                reason="retry_delay",
                decided_at=dt.datetime.now(dt.UTC),
                detail=f"повтор через {settings.market_data_retry_after_minutes} мин",
            )

    return ready, last_closed


def _after_retry_delay(
    missing: list[dt.date],
    last_attempt: dict[dt.date, dt.datetime],
    settings: Settings,
    now: dt.datetime,
) -> list[dt.date]:
    """Отсеять сессии, которые пытались собрать слишком недавно.

    Без этого поиск нехватки по всему окну превращает устойчивую ошибку в
    непрерывный поток обращений: тик раз в минуту, сессия не собирается, и та же
    неудачная попытка повторяется шестьдесят раз в час по одному и тому же
    неотвечающему адресу (FR-029c).

    Отметка берётся из таблицы исходов, а не из памяти процесса: перезапуск не
    должен обнулять выдержку — иначе цикл падений и рестартов даёт тот же
    поток.
    """
    delay = dt.timedelta(minutes=settings.market_data_retry_after_minutes)
    if delay <= dt.timedelta(0):
        return missing

    # Момент может прийти наивным — так его передают проверки закрытости сессии.
    # Отметки в хранилище осведомлённые, и вычитание одного из другого падает.
    # Наивное время здесь означает московское: другого пояса у этого кода нет.
    current = now if now.tzinfo is not None else now.replace(tzinfo=MOSCOW)

    ready: list[dt.date] = []
    for day in missing:
        attempted = last_attempt.get(day)
        if attempted is not None and current - attempted.astimezone(MOSCOW) < delay:
            logger.debug("сессия %s пропущена: попытка была %s", day, attempted)
            continue
        ready.append(day)
    return ready


async def within_attempt_limit(
    repository: MarketDataRepository,
    missing: list[dt.date],
    settings: Settings,
) -> list[dt.date]:
    """Сессии, не исчерпавшие предел попыток.

    Отличается от :func:`selectable` ровно одним: выдержка после неудачи здесь
    не учитывается. Сессию, ждущую повтора, сбор возьмёт сам — просто позже, —
    и смешивать её с исчерпавшей попытки нельзя: во втором случае без человека
    не обойтись, в первом вмешиваться не нужно (FR-054).
    """
    attempts = await repository.attempts_by_session(missing)
    return [
        day for day in missing if attempts.get(day, 0) < settings.market_data_session_max_attempts
    ]


async def selectable(
    repository: MarketDataRepository,
    missing: list[dt.date],
    settings: Settings,
    moment: dt.datetime | None = None,
) -> list[dt.date]:
    """Из недостающих сессий — те, которые сбор ДЕЙСТВИТЕЛЬНО возьмёт.

    Правило одно на работу и на обещание. Раздел называл ближайшую недостающую
    сессию независимо от того, возьмут её или нет, — а сбор пропускает
    ждущую выдержки после неудачи и исчерпавшую предел попыток, и экран
    обещал одно, пока система брала другое (FR-054).

    Записей здесь не делается: объявление причины пропуска принадлежит сбору,
    а не сводке, которую читают по нескольку раз в минуту.
    """
    if not missing:
        return []

    within_limit = await within_attempt_limit(repository, missing, settings)

    # Отметка времени берётся по котировкам: `ingest_session` гонит все источники
    # за дату одним заходом и котировки — первыми, поэтому их последняя попытка и
    # есть «когда мы в последний раз брались за этот день», какой бы источник ни
    # оставался незакрытым.
    return _after_retry_delay(
        within_limit,
        await repository.last_attempt_by_session(within_limit, equity_d1.SOURCE_ID),
        settings,
        moment or moscow_now(),
    )


async def calendar_is_due(
    repository: MarketDataRepository,
    now: dt.datetime | None,
    settings: Settings | None = None,
) -> bool:
    """Пора ли спрашивать календарь.

    Раз в сутки — мало. Первый тик суток приходится на 00:0x, когда сегодняшних
    торгов ещё не было, а повтор в тот же день запрещался: сегодняшняя дата
    попадала в календарь только следующей ночью, и сессия собиралась на сутки
    позже. Порог 19:30 при этом не работал вовсе.

    Поэтому правил два: спрашивать, если сегодня ещё не спрашивали, И спрашивать
    ещё раз, если с прошлого запроса наступил порог — прежде чем решать, что
    сегодняшней сессии не было (spec 008, FR-040).
    """
    last = await repository.last_successful_run_at(trading_calendar.SOURCE_ID)
    if last is None:
        return True

    moment = now or moscow_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MOSCOW)
    asked = last.astimezone(MOSCOW)

    if asked.date() < moment.date():
        return True

    if settings is None:
        return False

    threshold = dt.datetime.combine(moment.date(), _threshold_time(settings), tzinfo=MOSCOW)
    return asked < threshold <= moment


def _threshold_time(settings: Settings) -> dt.time:
    """Время, с которого сегодняшняя сессия считается закрытой."""
    raw = settings.market_data_ingest_after_close
    try:
        hours, minutes = raw.split(":", 1)
        return dt.time(int(hours), int(minutes))
    except (ValueError, IndexError):
        return dt.time(19, 30)


async def advance(
    session: AsyncSession,
    settings: Settings,
    now: dt.datetime | None = None,
    *,
    on_plan: Callable[[list[dt.date], str], None] | None = None,
    on_session_start: Callable[[dt.date], None] | None = None,
    on_session_done: Callable[[dt.date, bool | str], None] | None = None,
    on_source: Callable[[str, str, object], None] | None = None,
    on_skip: Callable[[dt.date, str, str | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AdvanceResult:
    """Синхронизировать календарь и собрать недостающие закрытые сессии.

    События о ходе работы — те же три, что у управляемого догона: план, начало
    сессии, её исход. Благодаря им автоматический сбор виден в общем баннере
    процессов ровно так же, как сбор по кнопке: человек не должен гадать, идёт
    ли работа, по тому, каким путём она запущена.
    """
    repository = MarketDataRepository(session)

    # Шаг 1: календарь. Без него новые сессии системе неизвестны: пропуски
    # считаются по сохранённому календарю, и пока он не обновлён, работы не
    # видно — а значит и календарь не обновится. Замкнутый круг разрывается
    # здесь.
    #
    # Спрашивается не чаще раза в сутки: календарь меняется раз в день, а тик
    # планировщика — раз в минуту, и обращение на каждый тик было бы тысячей
    # запросов к бирже заведомо ни за чем. Признак берётся из хранилища исходов,
    # а не из памяти процесса: перезапуск не должен его терять.
    # Идентификатор прогона заводится ЗДЕСЬ и служит всем сессиям этого
    # прохода: журнал группирует исходы по прогону и считает в нём сессии, а
    # при идентификаторе на сессию отставание в восемьдесят дней показывалось
    # восемьюдесятью прогонами по одному дню (FR-052).
    run_id = str(uuid.uuid4())

    # Исход календаря — не для журнала одного, а и для плана на экране. План
    # заводится ниже, когда работа найдена, поэтому исход запоминается и
    # объявляется после него (FR-056).
    # Не спрошенный сегодня календарь в план этого прогона не попадает вовсе.
    # Строка «уже спрошен сегодня» отвечала на вопрос, которого никто не
    # задавал: план — это то, что прогон делает, а не перечень всего, что
    # бывает (FR-007).
    calendar_state: tuple[str, object | None] = (ingest.STATUS_OMITTED, None)

    if await calendar_is_due(repository, now, settings):
        config = ingest.build_iss_config(settings)
        # Признак остановки — и здесь: шесть попыток с нарастающей паузой у
        # календаря ждут ровно столько же, сколько у любого другого источника
        # (FR-058j).
        async with IssClient(config, should_stop=should_stop) as iss:
            # Исход записывается тем же способом, что у остальных источников:
            # по этой записи и решается, пора ли спрашивать снова. Без неё
            # отметка «сегодня уже спрашивали» не существовала бы в тихом
            # состоянии, когда собирать нечего и `ingest_session` не вызывается.
            outcome = await ingest.run_source(
                repository,
                run_id,
                trading_calendar.SOURCE_ID,
                None,
                lambda: trading_calendar.sync_trading_calendar(
                    iss, repository, settings.market_data_calendar_proxy_security
                ),
            )
        calendar_state = (outcome.status, outcome)
        await session.commit()

    pending, last_closed = await pending_sessions(session, settings, now)
    if not pending:
        return AdvanceResult(last_closed_session=last_closed)

    # Два правила вместо одного, и порядок здесь — их следствие.
    #
    # **Свежая сессия не ждёт разбора истории.** При отставании порядок «от
    # старых к новым» отдаёт сегодняшние данные последними, а нужны они модели
    # сегодня: на стенде 2026-09-18 собранное кончалось 11.09 при календаре до
    # 17.09.
    #
    # **История идёт по порядку — от старых к новым.** Прогресс, ползущий
    # справа налево по шкале, которую читают слева направо, выглядит
    # неисправностью; после остановки заполнение шло и вовсе с обоих концов.
    #
    # Совместить их в одной последовательности нельзя, поэтому они разведены
    # по прогонам: есть и свежая сессия, и история — свежая собирается ОДНА и
    # первой, история достаётся следующему проходу (FR-045).
    pending = sorted(pending)

    # Что пропущено из-за предела: остаётся в ответе, даже если последняя
    # закрытая сессия всё-таки собрана.
    skipped_history: list[dt.date] = []

    limit = settings.startup_recovery_max_sessions
    if len(pending) > limit:
        logger.warning(
            "данные отстают на %d сессий при пределе %d: автоматическое "
            "восстановление не выполняется, нужен управляемый догон",
            len(pending),
            limit,
        )
        # Пропускается ИСТОРИЯ, а не сегодня. Прежде превышение предела
        # останавливало сбор целиком: система переставала собирать и текущие
        # данные, и отставание только росло (FR-046).
        history = [day for day in pending if day != last_closed]
        for day in history:
            await repository.record_skip(
                session_date=day,
                reason="gap_over_limit",
                decided_at=dt.datetime.now(dt.UTC),
                detail=f"разрыв {len(pending)} сессий при пределе {limit}",
            )
            if on_skip is not None:
                on_skip(day, "gap_over_limit", f"разрыв {len(pending)} сессий при пределе {limit}")
        await session.commit()

        skipped_history = history
        pending = [day for day in pending if day == last_closed]
        if not pending:
            return AdvanceResult(
                last_closed_session=last_closed,
                pending=history,
                gap_sessions=len(history),
                limit_exceeded=True,
            )

    # История, отложенная до следующего прохода. Это НЕ превышение предела: там
    # разрыв отвергается с объяснением, здесь работа просто разделена надвое.
    deferred: list[dt.date] = []

    # Свежая сессия собирается ОДНА и первой — после проверки предела, а не
    # до неё: предел считается по всему разрыву, и разделение выше отменяло бы
    # его вместе с объяснением пропуска (FR-046).
    if len(pending) > 1 and pending[-1] == last_closed:
        logger.info(
            "сбор: сначала свежая сессия %s, история (%d сессий) — следующим проходом",
            pending[-1],
            len(pending) - 1,
        )
        deferred = list(pending[:-1])
        pending = [pending[-1]]

    if on_plan is not None:
        # Идентификатор прогона уходит вместе с планом: по нему продолжение
        # узнаёт собранное этим же прогоном и не переспрашивает его (FR-058e).
        on_plan(list(pending), run_id)

    # Календарь синхронизирован ДО цикла сессий, и без этого объявления он
    # оставался в плане вечно ожидающим — то есть вечно «следующим», сколько бы
    # сессий прогон ни шёл (FR-056).
    if on_source is not None:
        on_source(trading_calendar.SOURCE_ID, calendar_state[0], calendar_state[1])

    collected: list[dt.date] = []
    for day in pending:
        # Проверка между сессиями — грубая: внутри сессии признак смотрится
        # ещё и между обращениями к бирже (FR-044). Без этого остановка
        # автосбора ждала всю сессию, а сессия с позициями идёт по обращению на
        # каждый из десятков контрактов — минуты после нажатия.
        if should_stop is not None and should_stop():
            logger.info("сбор остановлен перед сессией %s", day)
            break

        if on_session_start is not None:
            on_session_start(day)

        result = await ingest.ingest_session(
            session, settings, day, on_source=on_source, should_stop=should_stop, run_id=run_id
        )
        if result.succeeded:
            collected.append(day)
        else:
            logger.warning("сессия %s не собрана полностью: %s", day, result.unfinished_sources)

        if on_session_done is not None:
            # Прерванная сессия — третий исход, а не разновидность несобранной:
            # её план не доработан по команде, и доделать его обязано
            # продолжение. Автоматический путь помечал её несобранной, и
            # продолжение её не брало: ручной чинили, этот — нет (FR-058).
            on_session_done(day, ingest.INTERRUPTED if result.interrupted else result.succeeded)

    unfinished = [day for day in pending if day not in collected] + skipped_history + deferred
    return AdvanceResult(
        last_closed_session=last_closed,
        collected=collected,
        pending=unfinished,
        gap_sessions=len(unfinished),
        limit_exceeded=bool(skipped_history),
    )
