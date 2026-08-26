"""Единственность выполняющейся синхронизации (FR-029, FR-033).

Одновременно может выполняться только одна синхронизация состояния счёта.
Повторный запрос во время текущей не запускает второй broker request, а
использует результат уже выполняющейся операции.

Два уровня:

* ``asyncio.Lock`` — внутри процесса; его достаточно при одной реплике Worker;
* PostgreSQL advisory lock — на случай нескольких реплик или перекрытия
  деплоев (добавляется в US3 вместе с ручной синхронизацией).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


class SingleFlight[T]:
    """Схлопывает конкурентные вызовы в одно выполнение.

    Первый вызывающий выполняет операцию, остальные дожидаются её результата.
    Это ровно требуемое поведение: обращение к брокеру одно, а ответ получают
    все, кто его запросил.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current: asyncio.Task[T] | None = None

    @property
    def in_progress(self) -> bool:
        return self._current is not None and not self._current.done()

    async def run(self, operation: Callable[[], Coroutine[Any, Any, T]]) -> tuple[T, bool]:
        """Выполняет операцию или присоединяется к уже идущей.

        Возвращает результат и признак того, что он получен от чужого
        выполнения (``deduplicated``).
        """
        async with self._lock:
            task = self._current
            if task is not None and not task.done():
                joined = True
            else:
                task = asyncio.create_task(operation())
                self._current = task
                joined = False

        try:
            result = await asyncio.shield(task)
        finally:
            async with self._lock:
                if self._current is task and task.done():
                    self._current = None

        return result, joined
