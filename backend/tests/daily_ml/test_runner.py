"""Выполнение прогона Daily ML (US1, FR-012–FR-017).

Цикл должен замкнуться сам: готовая дата — задание — ранжирование —
сохранённый результат. И должен честно падать, не трогая собранные данные.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import reconcile as reconcile_module
from financial_ai.daily_ml import runner
from financial_ai.daily_ml.models import RunStatus
from financial_ai.daily_ml.repository import DailyMlRepository

from .conftest import ASOF, MODEL_ID, MODEL_VERSION, RANKINGS_URL, ranking_payload, seed

pytestmark = pytest.mark.db


async def test_ready_date_becomes_a_queued_run(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Готовая дата без успешного прогона превращается в задание."""
    await seed(db_session)

    result = await reconcile_module.reconcile(db_session, settings)

    assert result.latest_data_ready == ASOF
    assert result.queued == 1

    run = await DailyMlRepository(db_session).next_queued()
    assert run is not None
    assert run.asof_date == ASOF
    assert run.status == RunStatus.QUEUED.value
    assert (run.model_id, run.model_version) == (MODEL_ID, MODEL_VERSION)


async def test_run_goes_queued_running_success(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Прогон проходит состояния и сохраняет результат."""
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    repository = DailyMlRepository(db_session)
    run = await repository.next_queued()
    assert run is not None

    assert await runner.execute(db_session, settings, run)

    done = await repository.get(run.id)
    assert done is not None
    assert done.status == RunStatus.SUCCESS.value
    assert done.started_at is not None
    assert done.finished_at is not None
    assert done.included_asset_count == 1

    items = await repository.items(run.id)
    assert [item.rank for item in items] == [1]
    assert items[0].asset_id == "EQ_AST_SBER"
    # Скор хранится точно: он участвует в сортировке.
    assert str(items[0].score) == "0.500000000"


async def test_latest_success_moves_forward(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """После успеха последняя успешная дата модели — это дата прогона."""
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)
    run = await DailyMlRepository(db_session).next_queued()
    assert run is not None
    await runner.execute(db_session, settings, run)

    latest = await DailyMlRepository(db_session).latest_success()
    assert latest is not None
    assert latest.asof_date == ASOF


async def test_unavailable_link_fails_the_run_without_touching_data(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Отказ звена — это отказ прогона, а не потеря данных."""
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    repository = DailyMlRepository(db_session)
    run = await repository.next_queued()
    assert run is not None

    ranking_link.post(RANKINGS_URL).mock(side_effect=httpx.ConnectError("нет соединения"))

    assert not await runner.execute(db_session, settings, run)

    failed = await repository.get(run.id)
    assert failed is not None
    assert failed.status == RunStatus.FAILED.value
    assert failed.error_code == "ranking_unavailable"

    # Причина пригодна для показа: ни адреса, ни текста исключения.
    assert failed.error_message is not None
    assert "ConnectError" not in failed.error_message
    assert "http" not in failed.error_message

    # Собранные данные не тронуты.
    from financial_ai.market_data.repository import MarketDataRepository

    assert await MarketDataRepository(db_session).has_any_daily_bars()


async def test_digest_mismatch_is_refused(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Ответ, относящийся к другому набору, принимать нельзя."""
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    repository = DailyMlRepository(db_session)
    run = await repository.next_queued()
    assert run is not None

    ranking_link.post(RANKINGS_URL).respond(json=ranking_payload("sha256:совсем-другой-набор"))

    assert not await runner.execute(db_session, settings, run)

    failed = await repository.get(run.id)
    assert failed is not None
    assert failed.status == RunStatus.FAILED.value
    assert failed.error_code == "ranking_unavailable"


async def test_queue_is_processed_one_by_one(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Очередь обрабатывается обработчиком, а не вызывающей стороной."""
    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    processed = await runner.process_queue(db_session, settings)

    assert processed == 1
    assert await DailyMlRepository(db_session).next_queued() is None


async def test_commands_do_not_wait_for_inference(
    db_session: AsyncSession, settings: Settings, ranking_link: respx.MockRouter
) -> None:
    """Команда отвечает по факту изменения состояния, а не по итогу расчёта.

    У эмулятора инференс занимает секунды, у настоящей модели — минуты.
    Синхронная обработка упиралась бы в таймаут HTTP: команда выполнялась, а
    вызывающий получал «сборщик недоступен». Наблюдалось на живом стенде.

    Проверяется контракт внутреннего маршрута: после ответа задание стоит в
    очереди, а его выполнение идёт своим ходом.
    """
    from financial_ai.worker.routes import daily_ml as routes

    await seed(db_session)
    await reconcile_module.reconcile(db_session, settings)

    repository = DailyMlRepository(db_session)
    run = await repository.next_queued()
    assert run is not None

    # Подталкивание очереди — фоновая задача: она регистрируется и не
    # выполняется внутри вызова.
    routes._kick(settings)
    assert routes._kicks

    for task in list(routes._kicks):
        task.cancel()


async def test_dataset_failure_reason_carries_no_internal_paths(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Причина отказа сборки набора не несёт путей и текста исключения.

    Заметки реконсиляции доходят до браузера через `/api/daily-ml/reconcile`, а
    `OSError` несёт путь внутреннего тома — `[Errno 13] Permission denied:
    '/datasets/…'`. Показывать его нельзя (FR-038); полная причина остаётся в
    журнале.
    """
    from financial_ai.ranking import dataset as dataset_module

    await seed(db_session)

    secret_path = "/datasets/2026-09-01-тайный-путь"

    async def failing(*args: object, **kwargs: object) -> object:
        raise dataset_module.DatasetError(f"не удалось записать набор: {secret_path}")

    monkeypatch.setattr(dataset_module, "build_dataset", failing)

    result = await reconcile_module.reconcile(db_session, settings)

    assert result.queued == 0
    assert all(secret_path not in note for note in result.notes)
    assert all("Errno" not in note for note in result.notes)
