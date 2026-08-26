"""Классификация сбоев обращения к брокеру.

Наружу уходит только код причины, а не текст ответа брокера (FR-028).
Диагностика санитизируется: значение токена в неё попасть не может
(FR-030, SC-009).
"""

from __future__ import annotations

from enum import StrEnum


class FailureReason(StrEnum):
    """Коды причин неуспешной синхронизации."""

    BROKER_UNAVAILABLE = "broker_unavailable"
    BROKER_REJECTED_TOKEN = "broker_rejected_token"
    RATE_LIMITED = "rate_limited"
    VALIDATION_FAILED = "validation_failed"
    INTERNAL_ERROR = "internal_error"


class BrokerError(Exception):
    """Базовая ошибка обращения к брокеру."""

    reason: FailureReason = FailureReason.INTERNAL_ERROR

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.reason.value)
        self.detail = detail


class BrokerUnavailableError(BrokerError):
    """Недоступность, таймаут, сетевая ошибка."""

    reason = FailureReason.BROKER_UNAVAILABLE


class BrokerTokenRejectedError(BrokerError):
    """Токен отсутствует, отозван, истёк или не имеет нужных прав."""

    reason = FailureReason.BROKER_REJECTED_TOKEN


class BrokerTokenMissingError(BrokerTokenRejectedError):
    """Токен не задан в конфигурации сервера.

    Отличается от отклонённого токена статусом подключения: пользователю
    сообщается, что доступ не сконфигурирован, а не что он отозван (FR-024).
    """


class BrokerRateLimitedError(BrokerError):
    """Превышены лимиты запросов к T-Invest API."""

    reason = FailureReason.RATE_LIMITED


class BrokerValidationError(BrokerError):
    """Ответ брокера не прошёл валидацию и не может быть сохранён (FR-004)."""

    reason = FailureReason.VALIDATION_FAILED
