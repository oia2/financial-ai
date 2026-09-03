"""Конфигурация эмулятора.

Секретов здесь нет и быть не может: эмулятору не нужны ни токены, ни доступ к БД,
ни выход в сеть.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки, приходящие из переменных окружения."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Идентификатор модели в ответе. У эмулятора он свой, чтобы выдачу нельзя было
    # спутать с выдачей настоящей модели.
    daily_ml_emulator_model_id: str = "daily-ml-emulator-v1"

    log_level: str = "INFO"


def load_settings() -> Settings:
    """Собрать настройки из окружения."""
    return Settings()
