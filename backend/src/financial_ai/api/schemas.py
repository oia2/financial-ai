"""Схемы ответов Backend-API.

Денежные, ценовые и количественные значения передаются **строками**:
JSON-число в JavaScript — это double, и на длинных портфелях он теряет
копейки (SC-002, SC-003). Форматирование под русскую локаль выполняет
frontend (FR-016).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer, StrictInt


def _decimal_to_str(value: Decimal) -> str:
    """Строковое представление без экспоненциальной записи."""
    return format(value, "f")


DecimalStr = Annotated[Decimal, PlainSerializer(_decimal_to_str, return_type=str)]
OptionalDecimalStr = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else _decimal_to_str(v), return_type=str | None),
]


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    display_name: str
    # Только маскированный вид: полный номер договора наружу не уходит (FR-022).
    masked_id: str
    currency: str


class BrokerOut(BaseModel):
    status: str
    account: AccountOut | None


class PositionOut(BaseModel):
    instrument_uid: str
    ticker: str | None
    name: str | None
    asset_type: str | None
    currency: str
    quantity: DecimalStr
    average_price: OptionalDecimalStr
    current_price: DecimalStr
    # НКД на одну облигацию, уже включённый в value. У прочих инструментов — 0.
    accrued_interest: DecimalStr
    value: DecimalStr
    unrealized_pnl: OptionalDecimalStr
    # Доля единицы: 0.0722 — это +7,22%. None, когда база неизвестна.
    unrealized_pnl_percent: OptionalDecimalStr
    share: DecimalStr


class SnapshotOut(BaseModel):
    captured_at: dt.datetime
    age_seconds: int
    total_value: DecimalStr
    cash: DecimalStr
    cash_share: DecimalStr
    unrealized_pnl: DecimalStr
    unrealized_pnl_percent: OptionalDecimalStr
    positions_count: int
    positions: list[PositionOut]


class SyncOut(BaseModel):
    status: str
    last_success_at: dt.datetime | None
    last_attempt_at: dt.datetime | None
    failure_reason_code: str | None
    is_stale: bool
    stale_after_seconds: int
    refresh_interval_seconds: int
    in_progress: bool


class PortfolioOut(BaseModel):
    broker: BrokerOut
    # None, если успешной синхронизации ещё не было (US4 AS4).
    snapshot: SnapshotOut | None
    sync: SyncOut


class RefreshResultOut(BaseModel):
    status: str
    deduplicated: bool
    captured_at: dt.datetime | None
    failure_reason_code: str | None


class RefreshIntervalOut(BaseModel):
    interval_seconds: int
    min_seconds: int
    max_seconds: int
    default_seconds: int


class RefreshIntervalIn(BaseModel):
    # Strict: строка «60» или дробное 60.5 — не целое число секунд и
    # принимаются быть не должны (US2 AS4). Диапазон проверяется отдельно,
    # чтобы ответ нёс код interval_out_of_range из контракта.
    interval_seconds: StrictInt
