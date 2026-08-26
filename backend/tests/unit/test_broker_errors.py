"""Классификация сбоев обращения к брокеру (T066).

Пользователю показывается код причины, а не текст ответа брокера (FR-028),
поэтому классификация должна быть однозначной.
"""

from __future__ import annotations

import pytest
from t_tech.invest.exceptions import (
    AioRequestError,
    RequestError,
    StatusCode,
    UnauthenticatedError,
)

from financial_ai.broker.errors import (
    BrokerRateLimitedError,
    BrokerTokenMissingError,
    BrokerTokenRejectedError,
    BrokerUnavailableError,
    BrokerValidationError,
    FailureReason,
)
from financial_ai.broker.tinvest import _classify


def _request_error(code: StatusCode) -> RequestError:
    return RequestError(code, "детали ответа брокера", None)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (StatusCode.UNAVAILABLE, FailureReason.BROKER_UNAVAILABLE),
        (StatusCode.DEADLINE_EXCEEDED, FailureReason.BROKER_UNAVAILABLE),
        (StatusCode.UNAUTHENTICATED, FailureReason.BROKER_REJECTED_TOKEN),
        (StatusCode.PERMISSION_DENIED, FailureReason.BROKER_REJECTED_TOKEN),
        (StatusCode.RESOURCE_EXHAUSTED, FailureReason.RATE_LIMITED),
        (StatusCode.INTERNAL, FailureReason.BROKER_UNAVAILABLE),
    ],
)
def test_grpc_status_maps_to_reason(code: StatusCode, expected: FailureReason) -> None:
    classified = _classify(_request_error(code))

    assert isinstance(classified, Exception)
    assert getattr(classified, "reason", None) is expected


def test_async_request_error_is_classified() -> None:
    classified = _classify(AioRequestError(StatusCode.RESOURCE_EXHAUSTED, "лимит", None))

    assert isinstance(classified, BrokerRateLimitedError)


def test_unauthenticated_exception_is_token_rejection() -> None:
    # В этой версии SDK UnauthenticatedError наследует RequestError
    # и требует те же аргументы.
    classified = _classify(UnauthenticatedError(StatusCode.UNAUTHENTICATED, "нет доступа", None))

    assert isinstance(classified, BrokerTokenRejectedError)


def test_unknown_exception_falls_back_to_unavailable() -> None:
    classified = _classify(RuntimeError("что-то пошло не так"))

    assert isinstance(classified, BrokerUnavailableError)


def test_broker_response_text_is_not_leaked_into_message() -> None:
    classified = _classify(_request_error(StatusCode.INTERNAL))

    # Наружу уходит код, а не текст ответа брокера (FR-028).
    assert "детали ответа брокера" not in str(classified)


def test_missing_token_is_distinct_from_rejected_token() -> None:
    missing = BrokerTokenMissingError("токен не задан")

    # Оба относятся к одной причине, но статус подключения будет разным:
    # «не сконфигурирован» против «отозван» (FR-024).
    assert isinstance(missing, BrokerTokenRejectedError)
    assert missing.reason is FailureReason.BROKER_REJECTED_TOKEN


def test_validation_error_has_its_own_reason() -> None:
    assert BrokerValidationError("суммы не сходятся").reason is FailureReason.VALIDATION_FAILED


def test_reason_codes_match_contract() -> None:
    # Значения кодов — часть контракта Backend-API.
    assert {reason.value for reason in FailureReason} == {
        "broker_unavailable",
        "broker_rejected_token",
        "rate_limited",
        "validation_failed",
        "internal_error",
    }
