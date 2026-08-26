"""Синхронизация состояния счёта с брокером.

Одна и та же функция обслуживает фоновое и ручное обновление (FR-006):
различается только то, кто её вызвал.

Неуспех брокера не приводит к потере данных: сохранённое состояние остаётся
нетронутым, обновляется только статус попытки (FR-008).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from financial_ai.broker.errors import BrokerError, BrokerTokenMissingError, FailureReason
from financial_ai.broker.protocol import BrokerPort
from financial_ai.broker.validation import validate_broker_snapshot
from financial_ai.db import repository
from financial_ai.db.engine import get_session_factory
from financial_ai.domain.portfolio import build_snapshot
from financial_ai.sync import advisory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Исход одной синхронизации."""

    status: str
    captured_at: dt.datetime | None = None
    failure_reason_code: str | None = None
    # True, если результат взят у уже выполнявшейся синхронизации и второго
    # обращения к брокеру не было (FR-029).
    deduplicated: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class SyncService:
    """Оркестрация: получить у брокера, проверить, сохранить."""

    def __init__(
        self,
        broker: BrokerPort,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self._broker = broker
        self._session_factory = session_factory or get_session_factory()

    async def sync_account_state(self) -> SyncResult:
        started = time.monotonic()

        # Межпроцессная страховка: если синхронизацию уже выполняет другая
        # реплика Worker, второго обращения к брокеру не будет — вернём
        # результат, который она сохранила (FR-029).
        async with self._session_factory() as lock_session:
            if not await advisory.try_acquire(lock_session):
                return await self._stored_result(started)

            try:
                return await self._perform(started)
            finally:
                await advisory.release(lock_session)

    async def _perform(self, started: float) -> SyncResult:
        try:
            snapshot = await self._broker.fetch_snapshot()
            validate_broker_snapshot(snapshot)
        except BrokerError as error:
            return await self._record_failure(error, started)

        account_snapshot = build_snapshot(snapshot)

        async with self._session_factory() as session, session.begin():
            await repository.save_snapshot(session, snapshot.account, account_snapshot)

        logger.info(
            "Состояние счёта обновлено",
            extra={
                "ctx_positions": account_snapshot.positions_count,
                "ctx_captured_at": account_snapshot.captured_at.isoformat(),
            },
        )

        return SyncResult(
            status="ok",
            captured_at=account_snapshot.captured_at,
            duration_ms=_elapsed_ms(started),
        )

    async def _stored_result(self, started: float) -> SyncResult:
        """Результат синхронизации, выполненной другим процессом."""
        async with self._session_factory() as session:
            sync_state = await repository.get_sync_state(session)
            state = await repository.get_state(session)

            return SyncResult(
                status=sync_state.last_status,
                captured_at=state.captured_at if state is not None else None,
                failure_reason_code=sync_state.failure_reason_code,
                deduplicated=True,
                duration_ms=_elapsed_ms(started),
            )

    async def _record_failure(self, error: BrokerError, started: float) -> SyncResult:
        # Отсутствие токена — это «доступ не сконфигурирован», а не «токен
        # отозван»: пользователь должен увидеть разные объяснения (FR-024).
        if isinstance(error, BrokerTokenMissingError):
            broker_status = "not_configured"
        elif error.reason is FailureReason.BROKER_REJECTED_TOKEN:
            broker_status = "rejected"
        else:
            broker_status = None

        async with self._session_factory() as session, session.begin():
            await repository.record_failure(
                session,
                reason_code=error.reason.value,
                detail=error.detail,
                broker_status=broker_status,
            )

        # Причина фиксируется для диагностики; значение токена сюда попасть
        # не может — фильтр секретов стоит на всех обработчиках (FR-030).
        logger.warning(
            "Синхронизация с брокером не удалась",
            extra={"ctx_reason": error.reason.value, "ctx_detail": error.detail},
        )

        return SyncResult(
            status="failed",
            failure_reason_code=error.reason.value,
            duration_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
