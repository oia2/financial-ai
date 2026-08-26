"""Фейковый брокер-адаптер.

Тесты подставляют его вместо T-Invest SDK: система знает о брокере только
протокол ``BrokerPort``, поэтому мокировать gRPC не требуется.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

from financial_ai.broker.errors import BrokerError
from financial_ai.domain.models import BrokerAccount, BrokerPosition, BrokerSnapshot

DEFAULT_ACCOUNT = BrokerAccount(
    broker_account_id="2000123456",
    masked_id="•• 3456",
    display_name="Основной брокерский счёт",
    currency="RUB",
)


def make_position(
    *,
    instrument_uid: str = "uid-sber",
    ticker: str | None = "SBER",
    name: str | None = "Сбербанк",
    asset_type: str | None = "share",
    currency: str = "RUB",
    quantity: str = "1200",
    average_price: str | None = "281.4",
    current_price: str = "301.72",
    accrued_interest: str = "0",
) -> BrokerPosition:
    return BrokerPosition(
        instrument_uid=instrument_uid,
        ticker=ticker,
        name=name,
        asset_type=asset_type,
        currency=currency,
        quantity=Decimal(quantity),
        average_price=None if average_price is None else Decimal(average_price),
        current_price=Decimal(current_price),
        accrued_interest=Decimal(accrued_interest),
    )


def make_snapshot(
    *,
    cash: str = "40545",
    positions: tuple[BrokerPosition, ...] | None = None,
    captured_at: dt.datetime | None = None,
    broker_total_value: str | None = None,
) -> BrokerSnapshot:
    items = (make_position(),) if positions is None else positions
    return BrokerSnapshot(
        account=DEFAULT_ACCOUNT,
        captured_at=captured_at or dt.datetime.now(dt.UTC),
        cash=Decimal(cash),
        positions=items,
        broker_total_value=None if broker_total_value is None else Decimal(broker_total_value),
    )


class FakeBroker:
    """Реализация ``BrokerPort`` с управляемым поведением."""

    def __init__(
        self,
        snapshot: BrokerSnapshot | None = None,
        error: BrokerError | None = None,
        delay: float = 0.0,
    ) -> None:
        self.snapshot = snapshot if snapshot is not None else make_snapshot()
        self.error = error
        self.delay = delay
        # Счётчик обращений — им проверяется дедупликация (FR-029).
        self.calls = 0

    async def fetch_snapshot(self) -> BrokerSnapshot:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.snapshot
