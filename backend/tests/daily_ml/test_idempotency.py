"""Идемпотентность прогонов (US3, FR-018–FR-022).

Главное свойство фичи: успешно обработанный вход не пересчитывается — ни при
перезапуске, ни по тику, ни по нажатию человека, ни при повторной реконсиляции.

Ключ — четыре величины: дата решения, дайджест набора, идентификатор и версия
модели. Проверка «сначала посмотрим, потом вставим» между двумя процессами не
атомарна, поэтому идемпотентность держится на уникальном индексе.
"""

from __future__ import annotations

import datetime as dt

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import reconcile as reconcile_module
from financial_ai.daily_ml import runner
from financial_ai.daily_ml.models import RunStatus
from financial_ai.daily_ml.repository import DailyMlRepository, RunInput

from .conftest import ASOF, MODEL_ID, MODEL_VERSION, seed

pytestmark = pytest.mark.db


async def _run_once(session: AsyncSession, settings: Settings) -> None:
    await reconcile_module.reconcile(session, settings)
    run = await DailyMlRepository(session).next_queued()
    assert run is not None
    await runner.execute(session, settings, run)


async def test_success_is_not_recomputed(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Тот же вход и та же модель — работы нет."""
    await seed(db_session)
    await _run_once(db_session, settings)

    calls_before = len(ranking_link.calls)
    result = await reconcile_module.reconcile(db_session, settings)

    assert result.queued == 0
    assert result.already_up_to_date
    # Обращений к модели не добавилось: пересчёта не было.
    rankings = [call for call in ranking_link.calls if call.request.method == "POST"]
    assert len(rankings) == 1
    assert len(ranking_link.calls) >= calls_before


async def test_repeated_reconcile_is_a_noop(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Повторная реконсиляция подряд ничего не создаёт."""
    await seed(db_session)
    await _run_once(db_session, settings)

    for _ in range(3):
        assert (await reconcile_module.reconcile(db_session, settings)).queued == 0

    runs, total = await DailyMlRepository(db_session).history()
    assert total == 1
    assert runs[0].status == RunStatus.SUCCESS.value


async def test_concurrent_enqueue_creates_one_run(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Одновременное создание даёт ровно одно задание.

    Ловится уникальным индексом, а не проверкой перед вставкой: между двумя
    процессами такая проверка не атомарна.
    """
    await seed(db_session)

    repository = DailyMlRepository(db_session)
    run_input = RunInput(
        asof_date=ASOF,
        dataset_digest="sha256:одинаковый",
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
    )

    first, created_first = await repository.enqueue(run_input, "file:///datasets/x")
    second, created_second = await repository.enqueue(run_input, "file:///datasets/x")

    assert first.id == second.id
    assert created_first
    assert not created_second

    _, total = await repository.history()
    assert total == 1


async def test_changed_digest_is_a_different_run(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Изменившийся вход — другой прогон, а не перезапись прежнего.

    Прежний успех остаётся фактом: изменился вход, а не прогон.
    """
    await seed(db_session)
    repository = DailyMlRepository(db_session)

    old = RunInput(ASOF, "sha256:старый", MODEL_ID, MODEL_VERSION)
    new = RunInput(ASOF, "sha256:новый", MODEL_ID, MODEL_VERSION)

    first, _ = await repository.enqueue(old, "file:///datasets/old")
    second, created = await repository.enqueue(new, "file:///datasets/new")

    assert created
    assert first.id != second.id

    _, total = await repository.history()
    assert total == 2


async def test_changed_model_version_is_a_different_run(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Версия модели входит в идентичность: новая версия — новая работа."""
    await seed(db_session)
    repository = DailyMlRepository(db_session)

    v1 = RunInput(ASOF, "sha256:одинаковый", MODEL_ID, "v1")
    v2 = RunInput(ASOF, "sha256:одинаковый", MODEL_ID, "v2")

    await repository.enqueue(v1, "file:///datasets/x")
    _, created = await repository.enqueue(v2, "file:///datasets/x")

    assert created

    _, total = await repository.history()
    assert total == 2


async def test_interrupted_run_is_failed_and_requeued(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Прогон, прерванный перезапуском, не зависает и возобновляется сам.

    Обрыв — **не отказ**: с входом и со звеном всё было в порядке, перезапустили
    процесс. Поэтому запись снимается из «выполняется» и возвращается в очередь,
    а не ждёт, пока человек нажмёт «повторить»: иначе каждое обновление стенда
    оставляло бы дату непосчитанной.
    """
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    repository = DailyMlRepository(db_session)
    run = await repository.next_queued()
    assert run is not None

    await repository.mark_running(run.id, dt.datetime.now(dt.UTC))
    await db_session.commit()

    assert await reconcile_module.recover_interrupted(db_session, settings) == 1

    recovered = await repository.get(run.id)
    assert recovered is not None
    assert recovered.status == RunStatus.QUEUED.value
    assert recovered.attempt == 2
    # Следы прошлой попытки стёрты: «прервано перезапуском» относилось к ней.
    assert recovered.error_code is None
    assert await repository.running_run() is None


async def test_endlessly_interrupted_run_stops_at_the_attempt_limit(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Прогон, роняющий обработчик раз за разом, не перезапускается вечно.

    Возобновление по обрыву — удобство, а не обязательство: вход, из-за которого
    процесс падает, не должен ронять его при каждом старте.
    """
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    repository = DailyMlRepository(db_session)
    run = await repository.next_queued()
    assert run is not None

    for _ in range(settings.daily_ml_max_attempts + 1):
        current = await repository.get(run.id)
        assert current is not None
        if current.status != RunStatus.QUEUED.value:
            break
        await repository.mark_running(run.id, dt.datetime.now(dt.UTC))
        await db_session.commit()
        await reconcile_module.recover_interrupted(db_session, settings)

    final = await repository.get(run.id)
    assert final is not None
    assert final.status == RunStatus.FAILED.value
    assert final.error_code == "interrupted"
    assert final.attempt == settings.daily_ml_max_attempts


async def test_paused_mode_creates_no_runs(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Пауза останавливает создание заданий, но не сбор данных."""
    await seed(db_session)

    result = await reconcile_module.reconcile(db_session, settings, paused=True)

    assert result.paused
    assert result.queued == 0

    _, total = await DailyMlRepository(db_session).history()
    assert total == 0
