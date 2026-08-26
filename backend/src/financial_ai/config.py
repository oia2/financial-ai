"""Конфигурация из переменных окружения.

Секреты никогда не хранятся в коде и не попадают в репозиторий (Принцип VII
Constitution). Токен брокера читается только процессом Backend-Worker; для
Backend-API он не задаётся и остаётся ``None``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Боевой контур T-Invest API. Песочница: sandbox-invest-public-api.tbank.ru:443
DEFAULT_TBANK_TARGET = "invest-public-api.tbank.ru:443"


class Settings(BaseSettings):
    """Настройки обоих backend-сервисов."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="postgresql+asyncpg://financial_ai:financial_ai_local@localhost:5432/financial_ai",
        description="DSN PostgreSQL для SQLAlchemy async.",
    )

    tbank_invest_read_token: SecretStr | None = Field(
        default=None,
        description=(
            "Персональный токен Т-Банк Invest с правами только на чтение. "
            "Задаётся ТОЛЬКО контейнеру backend-worker."
        ),
    )

    tbank_invest_target: str = Field(default=DEFAULT_TBANK_TARGET)

    worker_internal_url: str = Field(
        default="http://localhost:8000",
        description="Базовый адрес внутреннего REST Backend-Worker.",
    )

    worker_sync_timeout_seconds: float = Field(default=30.0, ge=1.0)

    log_level: str = Field(default="INFO")

    @property
    def broker_token_configured(self) -> bool:
        """Задан ли непустой токен.

        Возвращает факт наличия, а не значение: значение не покидает
        broker-адаптер (FR-023, SC-009).
        """
        token = self.tbank_invest_read_token
        return token is not None and bool(token.get_secret_value().strip())

    def broker_token_value(self) -> str | None:
        """Значение токена для broker-адаптера. Единственная точка доступа."""
        if not self.broker_token_configured:
            return None
        assert self.tbank_invest_read_token is not None
        return self.tbank_invest_read_token.get_secret_value().strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Настройки процесса. Кэшируются: конфигурация не меняется в рантайме."""
    return Settings()
