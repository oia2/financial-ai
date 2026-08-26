"""Одноразовая синхронизация состояния счёта.

    python -m financial_ai.sync.cli

Точка независимой проверки: позволяет получить состояние счёта до того, как
появились планировщик и внутренний REST, и пригодна для ручной диагностики
на работающей системе.
"""

from __future__ import annotations

import asyncio
import sys

from financial_ai.config import get_settings
from financial_ai.db.engine import dispose_engine
from financial_ai.logging import setup_logging
from financial_ai.sync.factory import build_sync_service


async def run() -> int:
    settings = get_settings()
    setup_logging(settings.log_level, secrets=[settings.broker_token_value() or ""])

    service = build_sync_service()
    try:
        result = await service.sync_account_state()
    finally:
        await dispose_engine()

    if result.ok:
        print(f"Состояние счёта обновлено: {result.captured_at:%Y-%m-%d %H:%M:%S} UTC")
        return 0

    # Наружу — код причины, а не текст ответа брокера (FR-028).
    print(f"Синхронизация не удалась: {result.failure_reason_code}", file=sys.stderr)
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
