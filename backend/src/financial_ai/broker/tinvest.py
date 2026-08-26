"""Адаптер T-Invest API.

Единственное место, где система знает об SDK ``t_tech.invest`` и о токене.
Наружу отдаются доменные модели; DTO SDK этот модуль не покидают.

Обращения строго на чтение (FR-005): используются только
``users.get_accounts``, ``operations.get_portfolio`` и справочник инструментов.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from t_tech.invest import AccountStatus, AsyncClient, InstrumentIdType
from t_tech.invest.exceptions import (
    AioRequestError,
    AioUnauthenticatedError,
    RequestError,
    StatusCode,
    UnauthenticatedError,
)

from financial_ai.broker.errors import (
    BrokerRateLimitedError,
    BrokerTokenMissingError,
    BrokerTokenRejectedError,
    BrokerUnavailableError,
)
from financial_ai.broker.money import to_decimal, to_decimal_or_zero
from financial_ai.config import Settings
from financial_ai.domain.models import BrokerAccount, BrokerPosition, BrokerSnapshot

logger = logging.getLogger(__name__)

# Позиции этого типа — денежные средства, а не инструменты портфеля:
# они отражаются отдельной метрикой и в таблицу позиций не попадают.
CURRENCY_INSTRUMENT_TYPE = "currency"

MASKED_ID_TAIL = 4


def _mask(account_id: str) -> str:
    """Маскированная идентификация счёта: наружу уходит только хвост (FR-022)."""
    tail = account_id[-MASKED_ID_TAIL:] if account_id else ""
    return f"•• {tail}" if tail else "••"


def _classify(error: Exception) -> Exception:
    """Переводит ошибку SDK в доменную с кодом причины."""
    if isinstance(error, UnauthenticatedError | AioUnauthenticatedError):
        return BrokerTokenRejectedError("брокер отклонил токен доступа")

    if isinstance(error, RequestError | AioRequestError):
        code = getattr(error, "code", None)
        if code in (StatusCode.UNAUTHENTICATED, StatusCode.PERMISSION_DENIED):
            return BrokerTokenRejectedError("брокер отклонил токен доступа")
        if code == StatusCode.RESOURCE_EXHAUSTED:
            return BrokerRateLimitedError("превышены лимиты запросов к T-Invest API")
        if code in (StatusCode.UNAVAILABLE, StatusCode.DEADLINE_EXCEEDED):
            return BrokerUnavailableError("T-Invest API недоступен")
        # Текст ответа брокера наружу не транслируется (FR-028): только код.
        return BrokerUnavailableError(f"обращение к брокеру завершилось с кодом {code}")

    return BrokerUnavailableError("не удалось обратиться к T-Invest API")


class TInvestBroker:
    """Реализация :class:`~financial_ai.broker.protocol.BrokerPort`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Названия инструментов меняются редко: справочник кэшируется на время
        # жизни процесса, чтобы не тратить лимиты запросов на каждый цикл.
        self._instrument_names: dict[str, str | None] = {}

    async def fetch_snapshot(self) -> BrokerSnapshot:
        token = self._settings.broker_token_value()
        if token is None:
            raise BrokerTokenMissingError("токен доступа не сконфигурирован")

        try:
            async with AsyncClient(token, target=self._settings.tbank_invest_target) as client:
                account = await self._select_account(client)
                portfolio = await client.operations.get_portfolio(
                    account_id=account.broker_account_id
                )
                positions = await self._build_positions(client, portfolio)
        except Exception as error:  # классифицируется в _classify
            raise _classify(error) from error

        return BrokerSnapshot(
            account=account,
            captured_at=dt.datetime.now(dt.UTC),
            cash=to_decimal_or_zero(portfolio.total_amount_currencies),
            positions=positions,
            broker_total_value=to_decimal(portfolio.total_amount_portfolio),
        )

    async def _select_account(self, client: Any) -> BrokerAccount:
        """Выбирает единственный счёт (FR-025).

        Несколько счетов у брокера — не ошибка: берётся первый открытый,
        выбор активного счёта в объём фичи не входит.
        """
        response = await client.users.get_accounts()
        accounts = list(response.accounts)
        if not accounts:
            raise BrokerTokenRejectedError("у токена нет доступа ни к одному счёту")

        opened = [a for a in accounts if a.status == AccountStatus.ACCOUNT_STATUS_OPEN]
        account = (opened or accounts)[0]

        if len(opened or accounts) > 1:
            logger.info(
                "У токена доступно несколько счетов, используется первый открытый",
                extra={"ctx_accounts_count": len(accounts)},
            )

        return BrokerAccount(
            broker_account_id=account.id,
            masked_id=_mask(account.id),
            display_name=account.name or "Брокерский счёт",
            currency="RUB",
        )

    async def _build_positions(self, client: Any, portfolio: Any) -> tuple[BrokerPosition, ...]:
        result: list[BrokerPosition] = []

        for raw in portfolio.positions:
            if raw.instrument_type == CURRENCY_INSTRUMENT_TYPE:
                # Денежные средства учтены в total_amount_currencies —
                # иначе они попали бы в итог дважды.
                continue

            quantity = to_decimal_or_zero(raw.quantity)
            current_price = to_decimal_or_zero(raw.current_price)
            average_price = to_decimal(raw.average_position_price)

            result.append(
                BrokerPosition(
                    instrument_uid=raw.instrument_uid or raw.figi,
                    ticker=raw.ticker or None,
                    name=await self._instrument_name(client, raw.instrument_uid),
                    asset_type=raw.instrument_type or None,
                    currency=_position_currency(raw),
                    quantity=quantity,
                    average_price=average_price,
                    current_price=current_price,
                )
            )

        return tuple(result)

    async def _instrument_name(self, client: Any, instrument_uid: str) -> str | None:
        """Человекочитаемое название инструмента (FR-002).

        Недоступность справочника не должна ломать синхронизацию: отсутствие
        названия — предусмотренный спецификацией случай, позиция отображается
        по тикеру или идентификатору.
        """
        if not instrument_uid:
            return None
        if instrument_uid in self._instrument_names:
            return self._instrument_names[instrument_uid]

        name: str | None = None
        try:
            response = await client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_UID,
                id=instrument_uid,
            )
            name = response.instrument.name or None
        except Exception:  # noqa: BLE001 — не критично для состояния счёта
            logger.warning(
                "Не удалось получить название инструмента",
                extra={"ctx_instrument_uid": instrument_uid},
            )

        self._instrument_names[instrument_uid] = name
        return name


def _position_currency(raw: Any) -> str:
    for field in ("current_price", "average_position_price"):
        value = getattr(raw, field, None)
        currency = getattr(value, "currency", None)
        if isinstance(currency, str) and currency:
            return currency.upper()
    return "RUB"
