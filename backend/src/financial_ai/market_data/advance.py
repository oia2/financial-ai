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
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import ingest
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

    raw = settings.market_data_ingest_after_close
    try:
        hours, minutes = raw.split(":", 1)
        after = dt.time(int(hours), int(minutes))
    except (ValueError, IndexError):
        after = dt.time(19, 30)

    return current.time() >= after


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

    # Два признака собранности, как у поиска пропусков в управляемом догоне:
    # наблюдения в таблице баров и успешный исход источника. Первое без второго
    # бывает после первичной загрузки, второе без первого — когда биржа честно
    # ответила пустотой.
    with_bars = await repository.sessions_with_daily_bars(closed)
    with_run = await repository.sessions_with_successful_run(closed, equity_d1.SOURCE_ID)

    missing = [day for day in closed if day not in with_bars and day not in with_run]
    if not missing:
        return [], last_closed

    return _after_retry_delay(
        missing,
        await repository.last_attempt_by_session(missing, equity_d1.SOURCE_ID),
        settings,
        moment,
    ), last_closed


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

    ready: list[dt.date] = []
    for day in missing:
        attempted = last_attempt.get(day)
        if attempted is not None and now - attempted.astimezone(MOSCOW) < delay:
            logger.debug("сессия %s пропущена: попытка была %s", day, attempted)
            continue
        ready.append(day)
    return ready


async def _calendar_is_due(repository: MarketDataRepository, now: dt.datetime | None) -> bool:
    """Пора ли спрашивать календарь.

    Раз в сутки достаточно: новые торговые дни появляются не чаще. Отметка —
    момент последнего успешного прогона источника календаря.
    """
    last = await repository.last_successful_run_at(trading_calendar.SOURCE_ID)
    if last is None:
        return True
    return last.astimezone(MOSCOW).date() < (now or moscow_now()).date()


async def advance(
    session: AsyncSession,
    settings: Settings,
    now: dt.datetime | None = None,
) -> AdvanceResult:
    """Синхронизировать календарь и собрать недостающие закрытые сессии."""
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
    if await _calendar_is_due(repository, now):
        config = ingest.build_iss_config(settings)
        async with IssClient(config) as iss:
            # Исход записывается тем же способом, что у остальных источников:
            # по этой записи и решается, пора ли спрашивать снова. Без неё
            # отметка «сегодня уже спрашивали» не существовала бы в тихом
            # состоянии, когда собирать нечего и `ingest_session` не вызывается.
            await ingest.run_source(
                repository,
                str(uuid.uuid4()),
                trading_calendar.SOURCE_ID,
                None,
                lambda: trading_calendar.sync_trading_calendar(
                    iss, repository, settings.market_data_calendar_proxy_security
                ),
            )
        await session.commit()

    pending, last_closed = await pending_sessions(session, settings, now)
    if not pending:
        return AdvanceResult(last_closed_session=last_closed)

    limit = settings.startup_recovery_max_sessions
    if len(pending) > limit:
        logger.warning(
            "данные отстают на %d сессий при пределе %d: автоматическое "
            "восстановление не выполняется, нужен управляемый догон",
            len(pending),
            limit,
        )
        return AdvanceResult(
            last_closed_session=last_closed,
            pending=pending,
            gap_sessions=len(pending),
            limit_exceeded=True,
        )

    collected: list[dt.date] = []
    for day in pending:
        result = await ingest.ingest_session(session, settings, day)
        if result.succeeded:
            collected.append(day)
        else:
            logger.warning("сессия %s не собрана полностью: %s", day, result.unfinished_sources)

    return AdvanceResult(
        last_closed_session=last_closed,
        collected=collected,
        pending=[day for day in pending if day not in collected],
        gap_sessions=len(pending) - len(collected),
    )
