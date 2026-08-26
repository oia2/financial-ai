"""Схема хранения по specs/001-investment-account-state/data-model.md.

Денежные, ценовые и количественные величины — ``NUMERIC(28, 9)``: точность
``nano`` из T-Invest API сохраняется без потерь, ``float`` не используется
(SC-002, SC-003). Время — ``TIMESTAMPTZ`` в UTC.

Пользователя как сущности на этом этапе не существует: есть только токен в
конфигурации сервера и ровно один счёт (FR-025), поэтому таблицы пользователей
и поля ``user_id`` здесь нет.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Единственная строка в singleton-таблицах.
SINGLETON_ID = 1

# 28 разрядов всего, 9 после запятой: nano-точность T-Invest плюс запас
# целой части для агрегатов.
MONEY = Numeric(28, 9)

INTERVAL_MIN_SECONDS = 15
INTERVAL_MAX_SECONDS = 3600
INTERVAL_DEFAULT_SECONDS = 60


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class InvestmentAccount(Base):
    """Единственный брокерский счёт."""

    __tablename__ = "investment_account"
    __table_args__ = (CheckConstraint("id = 1", name="ck_investment_account_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=SINGLETON_ID)
    broker_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Только маскированный вид: полный номер договора не хранится (FR-022, SC-009).
    masked_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="RUB")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=_utcnow
    )


class AccountState(Base):
    """Актуальное состояние счёта. Заменяется целиком (FR-007, FR-008)."""

    __tablename__ = "account_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_account_state_singleton"),
        CheckConstraint("positions_count >= 0", name="ck_account_state_positions_count"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=SINGLETON_ID)
    account_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("investment_account.id"), nullable=False
    )
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    total_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # База для процентного P&L. Ноль означает «процент не определён»,
    # а не «ноль процентов» (Edge Case спеки).
    positions_cost_basis: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    positions_count: Mapped[int] = mapped_column(Integer, nullable=False)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=_utcnow
    )

    positions: Mapped[list[PortfolioPosition]] = relationship(
        back_populates="state",
        cascade="all, delete-orphan",
        order_by="PortfolioPosition.sort_order",
        lazy="selectin",
    )


class PortfolioPosition(Base):
    """Позиция текущего состояния счёта."""

    __tablename__ = "portfolio_position"
    __table_args__ = (Index("idx_position_state", "state_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    state_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("account_state.id", ondelete="CASCADE"), nullable=False
    )

    instrument_uid: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Отрицательное количество допустимо — короткая позиция (Edge Case спеки).
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    average_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    current_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    state: Mapped[AccountState] = relationship(back_populates="positions")


class BrokerSyncState(Base):
    """Статус доступа к брокеру и последней попытки синхронизации (FR-009)."""

    __tablename__ = "broker_sync_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_broker_sync_state_singleton"),
        CheckConstraint(
            "broker_status in ('connected', 'not_configured', 'rejected')",
            name="ck_broker_sync_state_status",
        ),
        CheckConstraint("last_status in ('ok', 'failed')", name="ck_broker_sync_last_status"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=SINGLETON_ID)
    broker_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="not_configured")
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="failed")
    failure_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Санитизированная диагностика: значение токена сюда попасть не может.
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=_utcnow
    )


class AccountRefreshSettings(Base):
    """Настройка интервала автообновления (FR-031, FR-034)."""

    __tablename__ = "account_refresh_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_refresh_settings_singleton"),
        CheckConstraint(
            f"interval_seconds between {INTERVAL_MIN_SECONDS} and {INTERVAL_MAX_SECONDS}",
            name="ck_refresh_settings_interval_range",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=SINGLETON_ID)
    interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(INTERVAL_DEFAULT_SECONDS)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=_utcnow
    )
