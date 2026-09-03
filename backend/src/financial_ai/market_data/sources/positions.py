"""Позиции физических и юридических лиц по фьючерсам.

Самая тонкая из модальностей, и не из-за объёма кода.

**Задержанное прибытие.** Данные приходят позже закрытия сессии и годятся для
снимка на `asof_date = t`, только если успели до формирования набора. Если не
успели — они остаются наблюдением о дне `t`.

**Передатирование запрещено.** Опоздавший файл нельзя записать как наблюдение
о следующей сессии. Такая ошибка не падает тестом и не видна в данных: она
просто сдвигает историю на день, и модель обучается на смещённом сигнале.

**Отсутствие — не ноль.** Покрытие частичное по своей природе: позиции есть не
по всем активам. Ноль означал бы «участники не держат позиций», а пропуск —
«мы не знаем». Для модели это разные утверждения, и ветка позиций не должна
кодировать артефакт покрытия как сигнал.

Перенесено из `pipelines/moex_futures_positions/` (`MR-MASTER-DRO`, `f07295e`)
в части получения и разбора. Политика алиасов и сшивка ценовых рядов не
переносятся — они относятся к инженерии признаков и живут на стороне модели.
"""

from __future__ import annotations

import datetime as dt
import logging

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository, PositionRow
from financial_ai.market_data.sources.equity_d1 import asset_id_for, to_decimal

logger = logging.getLogger(__name__)

SOURCE_ID = "futures_positions"
COLUMNS = ("SECID", "TRADEDATE", "FIZ_LONG", "FIZ_SHORT", "JUR_LONG", "JUR_SHORT")


async def sync_positions(
    client: IssClient, repository: MarketDataRepository, session_date: dt.date
) -> int:
    """Собрать позиции за одну торговую сессию.

    Дата наблюдения — та, за которую запрошены данные. Она не подменяется
    датой получения ни при каких обстоятельствах.
    """
    rows = await client.fetch_session_rows(session_date.isoformat(), COLUMNS)
    positions = rows_to_positions(rows, session_date)
    written = await repository.upsert_positions(positions)
    logger.info(
        "позиции за %s: получено %d, записано %d (покрытие частичное — это норма)",
        session_date,
        len(rows),
        written,
    )
    return written


def rows_to_positions(rows: list[dict[str, object]], session_date: dt.date) -> list[PositionRow]:
    """Преобразовать ответ биржи в наблюдения о позициях.

    ``session_date`` проставляется из аргумента, а не из строки ответа: так
    передатирование становится невозможным по построению, а не по договорённости.
    """
    out: list[PositionRow] = []
    seen: set[str] = set()

    for row in rows:
        secid = row.get("SECID")
        if not isinstance(secid, str) or not secid.strip():
            continue
        ticker = secid.strip().upper()
        if ticker in seen:
            continue
        seen.add(ticker)

        out.append(
            PositionRow(
                asset_id=asset_id_for(ticker),
                session_date=session_date,
                fiz_long=to_decimal(row.get("FIZ_LONG")),
                fiz_short=to_decimal(row.get("FIZ_SHORT")),
                jur_long=to_decimal(row.get("JUR_LONG")),
                jur_short=to_decimal(row.get("JUR_SHORT")),
            )
        )
    return out
