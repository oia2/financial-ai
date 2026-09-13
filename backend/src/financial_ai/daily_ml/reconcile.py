"""Что нужно сделать прямо сейчас.

Реконсиляция отвечает на один вопрос: есть ли работа. Она вызывается при
старте, по тику планировщика, после успешного сбора, после завершения догона и
по команде человека — и во всех случаях делает одно и то же.

Три правила, каждое оплачено опытом:

- **уже посчитанное не пересчитывается.** Ключ идентичности — дата, дайджест
  набора, модель и её версия; успешный прогон с тем же ключом означает, что
  работы нет;
- **исторические даты заданиями не становятся.** После догона за полгода
  ставится одна дата — последняя готовая. Иначе один клик по догону породил бы
  сотни обращений к модели;
- **догон живёт в сборе данных, а не здесь.** Реконсиляция разрыв не чинит и к
  бирже не ходит: она смотрит на уже собранное. Данные до последней закрытой
  сессии доводит `market_data.advance`, а разрыв сверх заданного предела
  показывается человеку и чинится управляемым догоном.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import readiness
from financial_ai.daily_ml.repository import DailyMlRepository, RunInput
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.ranking import client as ranking_client
from financial_ai.ranking import dataset as dataset_module

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Что реконсиляция нашла и что сделала."""

    queued: int = 0
    already_up_to_date: bool = False
    latest_data_ready: dt.date | None = None
    latest_ml_success: dt.date | None = None
    data_gap_sessions: int = 0
    paused: bool = False
    # Устарел ли вход последней готовой даты. `None` — реконсиляция до этого
    # вопроса не дошла (пауза, разрыв данных, пустой календарь), и «не знаю» не
    # выдаётся за «не устарел».
    stale_latest: bool | None = None
    notes: list[str] = field(default_factory=list)


async def recover_interrupted(session: AsyncSession, settings: Settings) -> int:
    """Пометить прогоны, оборванные перезапуском, и вернуть их в очередь.

    Обработчик один: `running` при старте однозначно означает обрыв. Догон в
    своё время ушёл от хранимого состояния именно потому, что «идёт» переживало
    падение; здесь состояние хранится, поэтому лечится явно.

    **Обрыв — не отказ.** С входом и со звеном всё было в порядке: перезапустили
    процесс. Поэтому работа возобновляется сама, а не ждёт, пока человек нажмёт
    «повторить»: иначе каждое обновление стенда оставляло бы дату непосчитанной.

    Предел попыток при этом соблюдается: прогон, который валит обработчик раз за
    разом, не должен перезапускаться бесконечно.
    """
    repository = DailyMlRepository(session)
    interrupted = await repository.fail_interrupted(dt.datetime.now(dt.UTC))
    if not interrupted:
        return 0

    requeued = 0
    for run_id in interrupted:
        run = await repository.get(run_id)
        if run is not None and run.attempt < settings.daily_ml_max_attempts:
            await repository.requeue(run_id)
            requeued += 1

    await session.commit()
    logger.warning(
        "прогонов прервано перезапуском: %d, возвращено в очередь: %d",
        len(interrupted),
        requeued,
    )
    return len(interrupted)


async def data_gap(session: AsyncSession, today: dt.date | None = None) -> int:
    """На сколько торговых сессий данные отстают от последней закрытой."""
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    last_known = await repository.latest_trading_session(today or moscow_today())
    if last_known is None:
        return 0

    window = await calendar.window(last_known, 400)
    collected = await repository.sessions_with_daily_bars(window)
    if not collected:
        return 0

    latest_collected = max(collected)
    return sum(1 for day in window if day > latest_collected)


async def reconcile(
    session: AsyncSession, settings: Settings, paused: bool = False
) -> ReconcileResult:
    """Найти работу и поставить её в очередь.

    Ранжирование восстанавливается независимо от сбора: если данные есть, а
    успешного прогона нет, выполняется только ранжирование. Это ровно случай
    «10.09 готово, 11.09 готово, ML отстал на день».
    """
    repository = DailyMlRepository(session)
    market = MarketDataRepository(session)

    latest_success = await repository.latest_success()
    latest_ml = latest_success.asof_date if latest_success else None

    gap = await data_gap(session)

    last_known = await market.latest_trading_session(moscow_today())
    if last_known is None:
        return ReconcileResult(
            already_up_to_date=True,
            latest_ml_success=latest_ml,
            notes=["календарь пуст: собирать и считать нечего"],
        )

    ready_date = await readiness.latest_ready(session, settings, last_known)
    if ready_date is None:
        return ReconcileResult(
            latest_ml_success=latest_ml,
            data_gap_sessions=gap,
            paused=paused,
            notes=["готовой даты нет: обязательный вход неполон"],
        )

    if paused:
        return ReconcileResult(
            latest_data_ready=ready_date,
            latest_ml_success=latest_ml,
            data_gap_sessions=gap,
            paused=True,
            notes=["автоматический режим на паузе"],
        )

    # Дешёвый вопрос перед дорогим. Пересборка набора ради дайджеста стоит
    # секунд процессорного времени, а тик — раз в минуту: за сутки это часы
    # работы ради вывода «ничего не изменилось». Если за датой уже есть успешный
    # прогон и после него ничего не собирали, вход измениться не мог.
    done = await repository.latest_success_for_date(ready_date)
    if done is not None and not await readiness.is_stale(
        session,
        settings,
        ready_date,
        done.dataset_digest,
        since=done.finished_at,
        window=(done.window_from, done.window_till),
        cheap_only=True,
    ):
        return ReconcileResult(
            queued=0,
            already_up_to_date=True,
            latest_data_ready=ready_date,
            latest_ml_success=latest_ml,
            data_gap_sessions=gap,
            stale_latest=False,
        )

    # Дайджест — часть идентичности, поэтому набор собирается до решения.
    # Он content-addressed: повторная сборка того же содержимого это no-op.
    try:
        dataset = await dataset_module.build_dataset(session, settings, ready_date)
    except dataset_module.DatasetError:
        # Причина не пересказывается наружу: заметки реконсиляции доходят до
        # браузера, а текст ошибки может нести путь внутреннего тома или адрес
        # службы (FR-038). Полная причина — в журнале.
        logger.exception("набор за %s не собран", ready_date)
        return ReconcileResult(
            latest_data_ready=ready_date,
            latest_ml_success=latest_ml,
            data_gap_sessions=gap,
            notes=[f"набор за {ready_date} не собран"],
        )

    identity = await _model_identity(session, settings)
    if identity is None:
        return ReconcileResult(
            latest_data_ready=ready_date,
            latest_ml_success=latest_ml,
            data_gap_sessions=gap,
            notes=["звено ранжирования не отвечает и его версия неизвестна"],
        )

    run_input = RunInput(
        asof_date=ready_date,
        dataset_digest=dataset.digest,
        model_id=identity[0],
        model_version=identity[1],
    )

    existing = await repository.find_by_input(run_input)

    # Устаревание считается **здесь**, где набор уже собран ради дайджеста.
    # Отдельно его считать нельзя: пересборка набора стоит секунд процессорного
    # времени, и на опрашиваемом маршруте она клала весь backend-api — 17 секунд
    # на запрос при опросе раз в три секунды. Здесь она уже оплачена.
    stale_latest = (
        existing is None or existing.status != "success"
    ) and await repository.latest_success_for_date(ready_date) is not None

    if existing is not None and existing.status == "success":
        # Тот же вход и та же модель — работа уже сделана. Это же условие
        # закрывает «устаревший вход»: при изменившемся дайджесте ключ другой,
        # и для последней готовой даты задание создастся.
        return ReconcileResult(
            queued=0,
            already_up_to_date=True,
            latest_data_ready=ready_date,
            latest_ml_success=latest_ml,
            data_gap_sessions=gap,
            stale_latest=False,
        )

    run, created = await repository.enqueue(
        run_input,
        dataset.ref,
        window=(dataset.sessions[0], dataset.sessions[-1]) if dataset.sessions else None,
        input_complete=readiness.dataset_is_complete(dataset.incomplete, settings),
    )
    await session.commit()

    if created:
        logger.info("ранжирование за %s поставлено в очередь", ready_date)

    return ReconcileResult(
        queued=1 if created or run.status == "queued" else 0,
        latest_data_ready=ready_date,
        latest_ml_success=latest_ml,
        data_gap_sessions=gap,
        stale_latest=stale_latest,
    )


async def _model_identity(session: AsyncSession, settings: Settings) -> tuple[str, str] | None:
    """Кто будет считать: идентификатор и версия модели.

    Спрашивается у звена, потому что версия входит в ключ идемпотентности и
    нужна ДО запроса. Если звено не отвечает, берётся последняя известная
    идентичность из истории: тогда задание всё же создаётся, честно падает с
    причиной «звено недоступно» и остаётся доступным для повтора. Без этого
    отказ звена не попадал бы в историю вовсе.
    """
    identity = await ranking_client.fetch_model_identity(settings)
    if identity is not None:
        return identity

    repository = DailyMlRepository(session)
    known = await repository.latest_success() or await repository.last_failure()
    if known is None:
        return None

    logger.warning("звено не ответило: используется последняя известная версия модели")
    return known.model_id, known.model_version
