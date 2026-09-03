"""Backend-Worker — синхронизация с T-Bank Invest API.

Единственный сервис, которому передаётся токен брокера. Наружу через nginx
не проксируется: внутренний REST доступен только внутри сети compose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from financial_ai.config import get_settings
from financial_ai.db.engine import dispose_engine
from financial_ai.logging import setup_logging
from financial_ai.market_data.scheduler import MarketDataScheduler
from financial_ai.sync.factory import build_sync_service
from financial_ai.sync.lock import SingleFlight
from financial_ai.sync.scheduler import SyncScheduler
from financial_ai.sync.service import SyncResult
from financial_ai.worker.routes import health, sync


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

    yield

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
