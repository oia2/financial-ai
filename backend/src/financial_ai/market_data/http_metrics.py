"""Измерение фактически отправленных HTTP-запросов.

Счётчик живёт на клиенте ровно один прогон. Он намеренно не является кешем и
не пытается выводить число запросов из числа инструментов или строк ответа:
одна логическая операция может включать страницу, повтор или поисковую пробу.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class HttpMetrics:
    """Факты обмена с внешним HTTP-источником за один прогон."""

    attempts: int = 0
    successful_responses: int = 0
    retries: int = 0
    search_probes: int = 0
    elapsed_seconds: float = 0.0

    def record_attempt(self, *, retry: bool, search_probe: bool, elapsed_seconds: float) -> None:
        self.attempts += 1
        self.retries += int(retry)
        self.search_probes += int(search_probe)
        self.elapsed_seconds += elapsed_seconds

    def record_success(self) -> None:
        self.successful_responses += 1

    def to_dict(self) -> dict[str, int | float]:
        return {
            "attempts": self.attempts,
            "successful_responses": self.successful_responses,
            "retries": self.retries,
            "search_probes": self.search_probes,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
        }
