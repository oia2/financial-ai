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

    # --- сбор рыночных данных MOEX (spec 003) -------------------------------
    # Данные MOEX ISS публичны: секретов среди этих настроек нет и быть не может.

    market_data_enabled: bool = Field(
        default=True,
        description="Выполнять ли ежедневный сбор рыночных данных.",
    )

    market_data_iss_base_url: str = Field(default="https://iss.moex.com/iss")

    market_data_board: str = Field(
        default="TQBR",
        description="Доска торгов: основной режим для акций в рублях.",
    )

    market_data_calendar_proxy_security: str = Field(
        default="SBER",
        description=(
            "Опорная бумага для построения торгового календаря. Даты, в которые "
            "она торговалась, и есть торговые сессии."
        ),
    )

    market_data_ingest_after_close: str = Field(
        default="19:30",
        description=(
            "Время запуска сбора после закрытия сессии, Europe/Moscow. Модель "
            "наблюдает ЗАВЕРШЁННУЮ сессию: собирать раньше — значит завести "
            "утечку будущего в признаки."
        ),
    )

    market_data_backfill_from: str = Field(
        default="",
        description="Начальная дата первичной загрузки. Пусто — вся доступная история.",
    )

    # Глубины окон выведены из конфигурации признаков модели, а не назначены:
    # самое длинное окно признака — 252 сессии, окно модели — 63 (20 у позиций),
    # отсюда 252 + 63 - 1 = 314 и 63 + 20 - 1 = 82 (research.md §1).
    market_data_price_window_sessions: int = Field(default=314, ge=1)
    market_data_global_window_sessions: int = Field(default=314, ge=1)
    market_data_positions_window_sessions: int = Field(default=82, ge=1)

    market_data_dataset_root: str = Field(
        default="/datasets",
        description="Каталог неизменяемых наборов входных данных.",
    )

    market_data_dataset_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "Срок хранения наборов. Неизменяемость означает накопление: без "
            "правила очистки место закончится."
        ),
    )

    # --- догон пропущенных сессий (spec 004) --------------------------------

    market_data_catchup_enabled: bool = Field(
        default=True,
        description=(
            "Догонять ли пропущенные торговые сессии. Выключение оставляет "
            "обнаружение пропусков: знать о дыре полезно, даже если чинить её "
            "решено вручную."
        ),
    )

    market_data_catchup_window_sessions: int = Field(
        default=0,
        ge=0,
        description=(
            "Глубина поиска пропусков в торговых сессиях. Ноль означает «как "
            "окно цен набора»: сессия старше него в набор не попадёт, и "
            "догонять её незачем. Другое значение имеет смысл только для "
            "отладки."
        ),
    )

    @property
    def catchup_window_sessions(self) -> int:
        """Фактическая глубина поиска пропусков.

        Предел выводится из устройства системы, а не назначается числом: дыра
        старше окна набора до модели не доходит.
        """
        return self.market_data_catchup_window_sessions or self.market_data_price_window_sessions

    market_data_http_timeout_seconds: float = Field(default=60.0, gt=0)
    market_data_http_retries: int = Field(default=6, ge=1)
    market_data_iss_page_limit: int = Field(default=100, ge=1)

    daily_ml_url: str = Field(
        default="http://daily-ml-emulator:8000",
        description="Базовый адрес звена ранжирования.",
    )

    daily_ml_timeout_seconds: float = Field(default=60.0, gt=0)

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
