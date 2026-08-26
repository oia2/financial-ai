"""Структурированное логирование с фильтром секретов.

Значение ``TBANK_INVEST_READ_TOKEN`` не должно появляться ни в одном журнале,
сообщении об ошибке или трейсбеке (FR-030, SC-009). Фильтр вырезает его из
сообщения, аргументов и текста исключения независимо от того, кто и как его
туда положил.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

REDACTED = "***REDACTED***"

# Токен короче этого не маскируется: слишком короткая строка дала бы ложные
# срабатывания на обычном тексте.
MIN_SECRET_LENGTH = 8


class SecretFilter(logging.Filter):
    """Вырезает известные секреты из всего, что уходит в лог."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s and len(s) >= MIN_SECRET_LENGTH]

    def add_secret(self, secret: str | None) -> None:
        if secret and len(secret) >= MIN_SECRET_LENGTH and secret not in self._secrets:
            self._secrets.append(secret)

    def scrub(self, value: str) -> str:
        """Публичный вариант: используется форматтером для готовой строки."""
        return self._scrub(value)

    def _scrub(self, value: str) -> str:
        for secret in self._secrets:
            if secret in value:
                value = value.replace(secret, REDACTED)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True

        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_any(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub_any(a) for a in record.args)

        if record.exc_text:
            record.exc_text = self._scrub(record.exc_text)

        return True

    def _scrub_any(self, value: Any) -> Any:
        return self._scrub(value) if isinstance(value, str) else value


class JsonFormatter(logging.Formatter):
    """Однострочный JSON — пригоден для машинного разбора.

    Секреты вырезаются здесь, из уже собранной строки. Фильтра на записи
    недостаточно: текст исключения формируется самим форматтером, а поля
    ``extra`` попадают в вывод, минуя обработку фильтра. Вырезание на
    последнем шаге закрывает оба пути.
    """

    def __init__(self, secret_filter: SecretFilter | None = None) -> None:
        super().__init__()
        self._secret_filter = secret_filter

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value

        line = json.dumps(payload, ensure_ascii=False)
        if self._secret_filter is not None:
            line = self._secret_filter.scrub(line)
        return line


_secret_filter = SecretFilter()


def get_secret_filter() -> SecretFilter:
    return _secret_filter


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    """Настраивает корневой логгер. Вызывается один раз при старте процесса."""
    for secret in secrets or []:
        _secret_filter.add_secret(secret)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(_secret_filter))
    handler.addFilter(_secret_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Фильтр вешается и на логгеры сторонних библиотек: исключения SDK могут
    # содержать метаданные запроса.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "t_tech"):
        logging.getLogger(name).addFilter(_secret_filter)
