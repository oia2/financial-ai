"""Backend-API — публичный HTTP API.

Этот сервис НЕ обращается к T-Bank API и не получает токен брокера
(FR-021, FR-023). Все данные читаются из PostgreSQL, обновление делегируется
Backend-Worker по внутреннему REST.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from financial_ai.api.routes import health
from financial_ai.config import get_settings
from financial_ai.db.engine import dispose_engine
from financial_ai.logging import setup_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    yield
    await dispose_engine()


app = FastAPI(
    title="Financial AI — Backend API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(health.router, prefix="/api")
