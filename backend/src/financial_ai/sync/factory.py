"""Сборка сервиса синхронизации.

Вынесено отдельно, чтобы точки входа (планировщик, внутренний REST, CLI)
собирали сервис одинаково, а тесты подставляли свой брокер-адаптер.
"""

from __future__ import annotations

from financial_ai.broker.protocol import BrokerPort
from financial_ai.broker.tinvest import TInvestBroker
from financial_ai.config import get_settings
from financial_ai.sync.service import SyncService


def build_broker() -> BrokerPort:
    return TInvestBroker(get_settings())


def build_sync_service(broker: BrokerPort | None = None) -> SyncService:
    return SyncService(broker or build_broker())
