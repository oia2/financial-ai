"""Токен не утекает никуда (T070, FR-023, FR-030, SC-009).

Проверяется весь путь: ответы API, журналы, диагностика в БД. Отдельно
проверяется худший случай — когда значение токена попало в текст исключения
стороннего SDK.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.broker.errors import BrokerTokenRejectedError, BrokerUnavailableError
from financial_ai.logging import JsonFormatter, get_secret_filter, setup_logging
from financial_ai.sync.service import SyncService
from tests.fakes.fake_broker import FakeBroker

pytestmark = pytest.mark.db

SECRET = "t.Kx9-secret-token-value-do-not-log"


@pytest.fixture
def captured_logs() -> StringIO:
    """Перехватывает журнал вместе с фильтром секретов."""
    setup_logging("INFO", secrets=[SECRET])

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(get_secret_filter()))
    handler.addFilter(get_secret_filter())

    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)


async def test_token_is_redacted_in_log_message(captured_logs: StringIO) -> None:
    logging.getLogger("test").warning("Ответ брокера: token=%s", SECRET)

    output = captured_logs.getvalue()

    assert SECRET not in output
    assert "***REDACTED***" in output


async def test_token_is_redacted_in_traceback(captured_logs: StringIO) -> None:
    try:
        raise RuntimeError(f"gRPC metadata: authorization=Bearer {SECRET}")
    except RuntimeError:
        logging.getLogger("test").exception("Сбой обращения к брокеру")

    output = captured_logs.getvalue()

    # Даже если SDK положил токен в исключение, наружу он не уйдёт.
    assert SECRET not in output
    assert "***REDACTED***" in output


async def test_failure_detail_from_broker_is_scrubbed_in_logs(
    db_session: AsyncSession,
    captured_logs: StringIO,
) -> None:
    broker = FakeBroker(error=BrokerUnavailableError(f"отказ при токене {SECRET}"))

    await SyncService(broker).sync_account_state()

    assert SECRET not in captured_logs.getvalue()


async def test_token_never_appears_in_api_responses(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    await SyncService(FakeBroker(error=BrokerTokenRejectedError(SECRET))).sync_account_state()

    portfolio = await api_client.get("/api/portfolio")
    health = await api_client.get("/api/health")

    assert SECRET not in portfolio.text
    assert SECRET not in health.text
    # Причина отдаётся кодом, а не текстом (FR-028).
    assert portfolio.json()["sync"]["failure_reason_code"] == "broker_rejected_token"


async def test_worker_health_reports_presence_only(
    worker_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TBANK_INVEST_READ_TOKEN", SECRET)

    from financial_ai.config import get_settings

    get_settings.cache_clear()
    try:
        response = await worker_client.get("/internal/health")
    finally:
        get_settings.cache_clear()

    assert SECRET not in response.text
    assert response.json()["broker_token"] == "configured"


async def test_full_account_number_is_never_exposed(
    api_client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    await SyncService(FakeBroker()).sync_account_state()

    response = await api_client.get("/api/portfolio")

    # Полный номер договора не хранится и не передаётся (FR-022).
    assert "2000123456" not in response.text
    assert response.json()["broker"]["account"]["masked_id"] == "•• 3456"

    stored = (
        await db_session.execute(text("select masked_id from investment_account where id = 1"))
    ).scalar()
    assert stored == "•• 3456"


async def test_settings_repr_hides_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TBANK_INVEST_READ_TOKEN", SECRET)

    from financial_ai.config import get_settings

    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert SECRET not in repr(settings)
        assert SECRET not in json.dumps(settings.model_dump(mode="json"), default=str)
        # Значение доступно только через явный вызов.
        assert settings.broker_token_value() == SECRET
    finally:
        get_settings.cache_clear()


async def test_formatter_scrubs_even_without_explicit_filter() -> None:
    """Обработчик, добавленный мимо setup_logging, не должен раскрывать токен.

    Именно так утечка и появилась в первый раз: форматтер без переданного
    фильтра молча ничего не вырезал.
    """
    setup_logging("INFO", secrets=[SECRET])

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())  # без аргументов — как сделал бы новый код

    logger = logging.getLogger("test.no-filter")
    logger.addHandler(handler)
    try:
        logger.warning("Токен в сообщении: %s", SECRET)
        logger.warning("Токен в extra", extra={"ctx_detail": SECRET})
        try:
            raise RuntimeError(SECRET)
        except RuntimeError:
            logger.exception("Токен в трейсбеке")
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()

    assert SECRET not in output
    assert output.count("***REDACTED***") >= 3
