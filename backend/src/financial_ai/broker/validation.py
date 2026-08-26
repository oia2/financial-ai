"""Валидация состояния, полученного от брокера (FR-004).

Состояние, не прошедшее проверку, не сохраняется как актуальное: пользователь
увидит предупреждение и прежние данные, а не подменённые неверными (FR-008,
Edge Case спецификации о несогласованных суммах).
"""

from __future__ import annotations

from decimal import Decimal

from financial_ai.broker.errors import BrokerValidationError
from financial_ai.domain.models import BrokerSnapshot
from financial_ai.domain.portfolio import build_snapshot

# Расхождение с итогом брокера, которое не считается ошибкой: копеечные
# округления и НКД. Берётся большее из абсолютного и относительного порога.
RECONCILIATION_ABSOLUTE_TOLERANCE = Decimal("1")
RECONCILIATION_RELATIVE_TOLERANCE = Decimal("0.005")


def validate_broker_snapshot(snapshot: BrokerSnapshot) -> None:
    """Проверяет полноту и внутреннюю согласованность. Бросает при нарушении."""
    account = snapshot.account
    if not account.broker_account_id:
        raise BrokerValidationError("пустой идентификатор счёта")
    if not account.currency:
        raise BrokerValidationError("не указана валюта счёта")

    seen: set[str] = set()
    for index, position in enumerate(snapshot.positions):
        where = f"позиция #{index}"

        if not position.instrument_uid:
            raise BrokerValidationError(f"{where}: пустой идентификатор инструмента")
        if position.instrument_uid in seen:
            raise BrokerValidationError(
                f"{where}: инструмент {position.instrument_uid} встречается дважды"
            )
        seen.add(position.instrument_uid)

        if not position.currency:
            raise BrokerValidationError(f"{where}: не указана валюта")
        if position.current_price < 0:
            raise BrokerValidationError(f"{where}: отрицательная текущая цена")
        if position.average_price is not None and position.average_price < 0:
            raise BrokerValidationError(f"{where}: отрицательная средняя цена")

    _validate_reconciliation(snapshot)


def _validate_reconciliation(snapshot: BrokerSnapshot) -> None:
    """Сверяет расчётный итог с итогом брокера.

    Отображаемая общая стоимость считается нами как сумма позиций и денежных
    средств — так суммы и доли всегда сходятся (SC-003). Итог брокера служит
    независимой проверкой: заметное расхождение означает, что ответ понят
    неверно, и сохранять его нельзя.
    """
    if snapshot.broker_total_value is None:
        return

    computed = build_snapshot(snapshot).total_value
    difference = abs(computed - snapshot.broker_total_value)
    tolerance = max(
        RECONCILIATION_ABSOLUTE_TOLERANCE,
        abs(snapshot.broker_total_value) * RECONCILIATION_RELATIVE_TOLERANCE,
    )

    if difference > tolerance:
        raise BrokerValidationError(
            f"расчётный итог {computed} расходится с итогом брокера "
            f"{snapshot.broker_total_value} на {difference}"
        )
