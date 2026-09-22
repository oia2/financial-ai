"""Backend-Worker — синхронизация с T-Bank Invest API.

Единственный сервис, которому передаётся токен брокера. Наружу через nginx
не проксируется: внутренний REST доступен только внутри сети compose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from financial_ai.config import get_settings
from financial_ai.daily_ml import reconcile as daily_ml_reconcile
from financial_ai.daily_ml.scheduler import DailyMlScheduler
from financial_ai.db.engine import dispose_engine, get_session_factory
from financial_ai.logging import setup_logging
from financial_ai.market_data.runner import CatchupRunner, CatchupStatus
from financial_ai.market_data.scheduler import MarketDataScheduler
from financial_ai.sync.factory import build_sync_service
from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.scheduler import SyncScheduler
from financial_ai.sync.service import SyncResult
from financial_ai.worker.routes import catchup, coverage, health, sync
from financial_ai.worker.routes import daily_ml as daily_ml_routes


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Токен передаётся в фильтр логов: даже если он попадёт в трейсбек SDK,
    # наружу уйдёт ***REDACTED*** (FR-030, SC-009).
    setup_logging(settings.log_level, secrets=[settings.broker_token_value() or ""])

    single_flight: SingleFlight[SyncResult] = SingleFlight()
    scheduler = SyncScheduler(build_sync_service(), single_flight)
    application.state.single_flight = single_flight
    application.state.scheduler = scheduler
    await scheduler.start()

    # Сбор рыночных данных: раз в торговую сессию, после её закрытия.
    # Секретов ему не нужно — данные MOEX ISS публичны.
    market_data = MarketDataScheduler(settings)
    application.state.market_data_scheduler = market_data
    await market_data.start()
    # Сводка выполняет серию согласованных чтений. Повторные запросы интерфейса
    # должны ждать тот же проход, а не занимать весь пул PostgreSQL.
    application.state.coverage_reports = coverage.CoverageReportFlight()

    # Догон истории НЕ запускается сам: он стартует только по команде человека.
    # Владелец заданияживёт в этом процессе, поэтому перезапуск снимает
    # состояние «идёт» сам собой.
    application.state.catchup_runner = CatchupRunner(settings)

    # Прогоны Daily ML, в отличие от догона, хранятся: это история решений.
    # Значит «выполняется», переживший падение, надо снять явно — иначе запись
    # останется в работе навсегда.
    factory = get_session_factory()
    async with factory() as session:
        await daily_ml_reconcile.recover_interrupted(session, settings)

    def collection_active() -> bool:
        """Идёт ли прямо сейчас сбор рыночных данных — любым из двух путей.

        Ранжированию это нужно, чтобы не искать работу посреди догона: готовая
        дата в этот момент движется, и тик, попавший в середину, ставил задание
        на раннюю дату.
        """
        return application.state.catchup_runner.is_active or (
            market_data.state.status is CatchupStatus.RUNNING
        )

    daily_ml = DailyMlScheduler(settings, collection_active=collection_active)
    application.state.daily_ml_scheduler = daily_ml
    await daily_ml.start()

    yield

    await daily_ml.stop()
    await application.state.catchup_runner.shutdown()
    await market_data.stop()

    # Остановка дожидается текущей синхронизации: транзакция не должна
    # оборваться на середине.
    await scheduler.stop()
    await dispose_engine()


app = FastAPI(
    title="Financial AI — Backend Worker (internal)",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/internal/docs",
    openapi_url="/internal/openapi.json",
)

app.include_router(health.router, prefix="/internal")
app.include_router(sync.router, prefix="/internal")
app.include_router(catchup.router, prefix="/internal")
app.include_router(coverage.router, prefix="/internal")
app.include_router(daily_ml_routes.router, prefix="/internal")
