"""Общие фикстуры тестов.

Интеграционные и контрактные тесты работают с настоящей PostgreSQL: схема
использует ``NUMERIC(28,9)``, CHECK-ограничения и advisory locks, которых нет
в SQLite. Если БД недоступна, такие тесты пропускаются с явным сообщением —
unit-тесты при этом продолжают выполняться.

Тестовая база — отдельная (``<имя>_test``), создаётся автоматически.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest

DEFAULT_DSN = "postgresql+asyncpg://financial_ai:financial_ai_local@localhost:5432/financial_ai"


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    base = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    parts = urlsplit(base)
    db_name = parts.path.lstrip("/") or "financial_ai"
    return urlunsplit(parts._replace(path=f"/{db_name}_test"))


TEST_DATABASE_URL = _test_database_url()

# Конфигурация процесса читается из окружения при первом обращении,
# поэтому переменные выставляются до импорта приложения.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("LOG_LEVEL", "WARNING")


def _admin_dsn(url: str) -> tuple[str, str]:
    """Возвращает DSN к базе postgres и имя тестовой базы."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    db_name = parts.path.lstrip("/")
    return urlunsplit(parts._replace(path="/postgres")), db_name


async def _ensure_database() -> bool:
    """Создаёт тестовую базу, если её нет. False — если PostgreSQL недоступна."""
    import asyncpg

    admin_dsn, db_name = _admin_dsn(TEST_DATABASE_URL)
    try:
        conn = await asyncpg.connect(admin_dsn, timeout=3)
    except Exception:  # noqa: BLE001 — недоступность БД не должна ломать unit-тесты
        return False

    try:
        exists = await conn.fetchval("select 1 from pg_database where datname = $1", db_name)
        if not exists:
            await conn.execute(f'create database "{db_name}"')
    finally:
        await conn.close()
    return True


@pytest.fixture(scope="session")
def database_available() -> bool:
    return asyncio.run(_ensure_database())


@pytest.fixture(scope="session")
def _schema(database_available: bool) -> Iterator[None]:
    if not database_available:
        yield
        return

    async def create() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        from financial_ai.db.models import Base

        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create())
    yield


@pytest.fixture
async def db_session(database_available: bool, _schema: None) -> AsyncIterator[object]:
    """Чистая БД с seed-строками, как после первой миграции."""
    if not database_available:
        pytest.skip(
            "PostgreSQL недоступна. Поднимите её: "
            "docker compose -f deployments/docker-compose/docker-compose.yml up -d postgres"
        )

    from sqlalchemy import text

    from financial_ai.db.engine import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "truncate portfolio_position, account_state, investment_account, "
                "broker_sync_state, account_refresh_settings restart identity cascade"
            )
        )
        await session.execute(
            text("insert into account_refresh_settings (id, interval_seconds) values (1, 60)")
        )
        await session.execute(
            text(
                "insert into broker_sync_state (id, broker_status, last_status, "
                "consecutive_failures) values (1, 'not_configured', 'failed', 0)"
            )
        )
        await session.commit()
        yield session


@pytest.fixture
async def api_client(db_session: object) -> AsyncIterator[object]:
    """HTTP-клиент Backend-API поверх тестовой БД."""
    import httpx

    from financial_ai.api.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def worker_client(db_session: object) -> AsyncIterator[object]:
    """HTTP-клиент внутреннего REST Backend-Worker."""
    import httpx

    from financial_ai.worker.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
        yield client
