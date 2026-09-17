"""Размер лота бумаг доски.

Лот — свойство инструмента на сегодня, а не ряд наблюдений: истории у него нет,
и в сводке он живёт вместе с прочими справочниками.

**Зачем он вообще.** На Московской бирже акции торгуются лотами: у Сбербанка
лот 10 акций, у ЛУКОЙЛа — 1. План портфеля в дробных акциях неисполним, а без
размера лота его нельзя выразить в штуках.

**Почему с биржи, а не от брокера.** Исследовательский репозиторий, откуда
переносится вся модельная логика, брокера не использует вовсе: весь рыночный
слой приходит с биржи. Размер лота он не добывает, а требует на входе
(`--lot-size`, обязательный аргумент модуля исполнения), поэтому источник обязан
появиться на нашей стороне. Биржевой делает план воспроизводимым и не создаёт
зависимости от брокера там, где её раньше не было.

**На готовность данных лот не влияет**: входом модели он не является, и его
отсутствие у отдельного актива не делает дату недобранной.
"""

from __future__ import annotations

import logging

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import asset_id_for

logger = logging.getLogger(__name__)

SOURCE_ID = "equity_lot_sizes"


async def sync_lot_sizes(client: IssClient, repository: MarketDataRepository) -> int:
    """Обновить размеры лотов известных активов.

    Возвращает число обновлённых строк. Активы, которых в хранилище ещё нет, не
    заводятся: справочник дополняет уже известное, а не подменяет собой сбор.
    """
    lots = await client.fetch_equity_lot_sizes()
    if not lots:
        logger.warning("размеры лотов: биржа вернула пустой перечень")
        return 0

    by_asset = {asset_id_for(ticker): lot for ticker, lot in lots.items()}
    updated = await repository.update_lot_sizes(by_asset)

    # Тем же ответом приходит ISIN: якорь сущности, по которому переименование
    # опознаётся как переименование. Отдельного обращения ради него не делается.
    isins = await client.fetch_equity_isins()
    known = await repository.tickers_with_history()
    linked = await repository.update_isins(
        {asset_id_for(ticker): isin for ticker, isin in isins.items() if ticker in known}
    )

    logger.info(
        "справочник бумаг: лотов обновлено %d из %d, ISIN записано %d",
        updated,
        len(lots),
        linked,
    )
    return updated
