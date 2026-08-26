"""Граница брокерской интеграции.

Всё, что система знает о брокере, — этот протокол. Тесты подставляют
собственную реализацию и не зависят от устройства SDK; замена брокера
означает новый адаптер и ничего больше.
"""

from __future__ import annotations

from typing import Protocol

from financial_ai.domain.models import BrokerSnapshot


class BrokerPort(Protocol):
    """Чтение состояния инвестиционного счёта.

    Обращения строго в режиме чтения: торговых операций фича не инициирует
    (FR-005).
    """

    async def fetch_snapshot(self) -> BrokerSnapshot:
        """Возвращает текущее состояние счёта.

        Бросает наследника ``BrokerError`` при любой неудаче — вызывающий код
        различает причины по ``reason``.
        """
        ...
