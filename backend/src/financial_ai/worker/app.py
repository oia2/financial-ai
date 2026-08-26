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
from financial_ai.worker.routes import health


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Токен передаётся в фильтр логов: даже если он попадёт в трейсбек SDK,
    # наружу уйдёт ***REDACTED*** (FR-030, SC-009).
    setup_logging(settings.log_level, secrets=[settings.broker_token_value() or ""])
    yield
    await dispose_engine()


app = FastAPI(
    title="Financial AI — Backend Worker (internal)",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/internal/docs",
    openapi_url="/internal/openapi.json",
)

app.include_router(health.router, prefix="/internal")
