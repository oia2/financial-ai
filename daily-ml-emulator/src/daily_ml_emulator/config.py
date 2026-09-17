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
    daily_ml_emulator_model_id: str = "daily-ml-emulator"

    # Версия модели. Входит в идентичность прогона наравне с датой решения и
    # дайджестом набора, поэтому объявляется явно, а не выводится из имени.
    daily_ml_emulator_model_version: str = "emulator-v1"

    # Имитация длительности инференса.
    #
    # Не украшение: без задержки состояние «выполняется» не наблюдаемо — прогон
    # проходит его за миллисекунды, и ни оркестрация, ни интерфейс на нём не
    # проверяются. Это единственное, что эмулятор знает о времени.
    daily_ml_emulator_latency_seconds: float = 2.0

    log_level: str = "INFO"


def load_settings() -> Settings:
    """Собрать настройки из окружения."""
    return Settings()
