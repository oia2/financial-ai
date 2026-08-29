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

    # Путь к файлу вселенной, по умолчанию — относительно рабочего каталога.
    # В образе WORKDIR = /app, поэтому разрешается в /app/universe/default.json;
    # при локальном запуске из daily-ml-emulator/ — в его universe/default.json.
    # Свой файл подставляется монтированием тома, без пересборки образа.
    daily_ml_emulator_universe_path: str = "universe/default.json"

    log_level: str = "INFO"


def load_settings() -> Settings:
    """Собрать настройки из окружения."""
    return Settings()
