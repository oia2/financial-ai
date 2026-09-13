"""Реконсиляция: что нужно сделать прямо сейчас (US3, FR-011, FR-027–FR-036).

Три правила проверяются здесь, и каждое оплачено опытом: ранжирование
восстанавливается независимо от сбора, исторические даты заданиями не
становятся, глубокий догон сам не запускается.
"""

from __future__ import annotations

import datetime as dt

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import reconcile as reconcile_module
from financial_ai.daily_ml import runner
from financial_ai.daily_ml.repository import DailyMlRepository

from .conftest import ASOF, SESSIONS, seed

pytestmark = pytest.mark.db


async def test_data_ready_but_ml_behind_runs_only_ml(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Данные есть, успешного прогона нет — выполняется только ранжирование.

    Это случай «10.09 готово, 11.09 готово, ML отстал на день»: заново
    скачивать данные не нужно.
    """
    await seed(db_session)

    result = await reconcile_module.reconcile(db_session, settings)

    assert result.latest_data_ready == ASOF
    assert result.latest_ml_success is None
    assert result.queued == 1
    # Обращений к бирже не было: реконсиляция данные не собирает.
    assert all("iss.moex.com" not in str(call.request.url) for call in ranking_link.calls)


async def test_second_reconcile_after_success_finds_nothing(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)
    run = await DailyMlRepository(db_session).next_queued()
    assert run is not None
    await runner.execute(db_session, settings, run)

    result = await reconcile_module.reconcile(db_session, settings)

    assert result.already_up_to_date
    assert result.latest_ml_success == ASOF


async def test_only_the_latest_ready_date_is_queued(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """После догона ставится одна дата — последняя готовая.

    Исторические даты заданиями не становятся: иначе один клик по догону
    породил бы сотни обращений к модели.
    """
    await seed(db_session)

    await reconcile_module.reconcile(db_session, settings)

    runs, total = await DailyMlRepository(db_session).history()
    assert total == 1
    assert runs[0].asof_date == ASOF
    assert runs[0].asof_date not in SESSIONS[:-1]


async def test_empty_calendar_is_not_an_error(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Пустой календарь — состояние системы, а не отказ."""
    result = await reconcile_module.reconcile(db_session, settings)

    assert result.queued == 0
    assert result.notes


async def test_incomplete_input_produces_no_run(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Инвариант: на неполном обязательном окне заданий не появляется."""
    await seed(db_session, collected=[SESSIONS[0], SESSIONS[1], ASOF])

    result = await reconcile_module.reconcile(db_session, settings)

    assert result.queued == 0
    assert result.latest_data_ready is None

    _, total = await DailyMlRepository(db_session).history()
    assert total == 0


async def test_data_gap_is_reported(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Отставание данных видно числом, а не догадкой."""
    await seed(db_session, collected=SESSIONS[:2])

    gap = await reconcile_module.data_gap(db_session, today=ASOF)

    assert gap == len(SESSIONS) - 2


async def test_interrupted_recovery_reports_count(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Без прерванных прогонов восстановление ничего не трогает."""
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    assert await reconcile_module.recover_interrupted(db_session, settings) == 0


async def test_link_unavailable_keeps_last_known_identity(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Звено молчит — задание всё равно создаётся и честно падает.

    Иначе отказ звена не попадал бы в историю вовсе, и человек не увидел бы ни
    причины, ни кнопки повтора.
    """
    import httpx

    from .conftest import HEALTH_URL

    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)
    run = await DailyMlRepository(db_session).next_queued()
    assert run is not None
    await runner.execute(db_session, settings, run)

    ranking_link.get(HEALTH_URL).mock(side_effect=httpx.ConnectError("нет связи"))

    # Новый вход: дата та же, дайджест другой — идентичность модели берётся из
    # истории, и задание создаётся.
    repository = DailyMlRepository(db_session)
    identity = await reconcile_module._model_identity(db_session, settings)

    assert identity == (run.model_id, run.model_version)
    assert await repository.latest_success() is not None


async def test_stale_input_is_computed_not_stored(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Устаревание вычисляется сравнением дайджестов.

    Хранимый признак пришлось бы обновлять у сотен записей при каждом закрытии
    дыры — вычисляемый расходиться не может.
    """
    from financial_ai.daily_ml import readiness

    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)
    run = await DailyMlRepository(db_session).next_queued()
    assert run is not None

    assert not await readiness.is_stale(db_session, settings, ASOF, run.dataset_digest)
    assert await readiness.is_stale(db_session, settings, ASOF, "sha256:другой")


async def test_missing_dataset_does_not_make_a_run_stale(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Набор больше не собирается — сравнивать не с чем, и прогон остаётся фактом."""
    from financial_ai.daily_ml import readiness

    assert not await readiness.is_stale(db_session, settings, dt.date(2020, 1, 9), "sha256:любой")


async def test_startup_tick_failure_does_not_stop_the_worker(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой восстановления при старте не уносит сборщик.

    Ранжирование — одна из работ worker'а, а не условие его существования:
    недоступное звено или нехватка прав на томе наборов не отменяют сбора
    рыночных данных и синхронизации счёта. Проверено на живом стенде: без этой
    защиты падение первого тика останавливало контейнер целиком.
    """
    from financial_ai.daily_ml.scheduler import DailyMlScheduler

    scheduler = DailyMlScheduler(settings)

    async def explode() -> None:
        raise PermissionError("нет прав на каталог наборов")

    monkeypatch.setattr(scheduler, "tick", explode)

    await scheduler.start()
    await scheduler.stop()
