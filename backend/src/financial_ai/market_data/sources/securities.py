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

from financial_ai.market_data.calendar import moscow_today
from financial_ai.market_data.iss.client import IssClient, ResponseContractError
from financial_ai.market_data.models import KIND_FUND, KIND_SHARE
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import asset_id_for
from financial_ai.market_data.sources.reference import ReferenceEmptyError

logger = logging.getLogger(__name__)

SOURCE_ID = "equity_lot_sizes"

# Вид бумаги по коду биржи (FR-060). `SECTYPE` списка доски: обыкновенная и
# привилегированная акция, депозитарная расписка — акции; паи биржевых,
# открытых, интервальных и закрытых ПИФов — фонды. Сверено 2026-09-24 по
# описаниям бумаг: OKEY (`D`) — `stock_dr`, TBEU (`J`) — `exchange_ppif`.
SECTYPE_KIND: dict[str, str] = {
    "1": KIND_SHARE,
    "2": KIND_SHARE,
    "D": KIND_SHARE,
    "9": KIND_FUND,
    "A": KIND_FUND,
    "B": KIND_FUND,
    "J": KIND_FUND,
}
# `GROUP` описания бумаги — для той, которой в списке доски уже нет.
GROUP_KIND: dict[str, str] = {
    "stock_shares": KIND_SHARE,
    "stock_dr": KIND_SHARE,
    "stock_ppif": KIND_FUND,
}


async def sync_lot_sizes(client: IssClient, repository: MarketDataRepository) -> int:
    """Обновить размеры лотов известных активов.

    Возвращает число обновлённых строк. Активы, которых в хранилище ещё нет, не
    заводятся: справочник дополняет уже известное, а не подменяет собой сбор.
    """
    lots = await client.fetch_equity_lot_sizes()
    if not lots:
        # Торгуемые бумаги без лотов не бывают: пустой перечень — отказ
        # источника, а не проверенный справочник. Прежде он возвращал ноль и
        # получал доказательство полноты, которое засчитывала готовность ML
        # и которое подавляло повтор до следующих суток (FR-033l).
        raise ReferenceEmptyError("размеры лотов: биржа вернула пустой перечень")

    # Ключ — СУЩНОСТЬ, а не имя. Обновление по несуществующему ключу не
    # затрагивает ни одной строки и молчит: у переименованной бумаги размер
    # лота переставал обновляться вовсе, и заметить это было нечем (FR-048).
    aliases = await repository.aliases_on(moscow_today())
    by_asset = {aliases.get(ticker, asset_id_for(ticker)): lot for ticker, lot in lots.items()}
    updated = await repository.update_lot_sizes(by_asset)

    # Тем же ответом приходит ISIN: якорь сущности, по которому переименование
    # опознаётся как переименование. Отдельного обращения ради него не делается.
    isins = await client.fetch_equity_isins()
    known = await repository.tickers_with_history()
    linked = await repository.update_isins(
        {
            aliases.get(ticker, asset_id_for(ticker)): isin
            for ticker, isin in isins.items()
            if ticker in known or ticker in aliases
        }
    )

    kinds = await _sync_security_kinds(client, repository, aliases)

    logger.info(
        "справочник бумаг: лотов обновлено %d из %d, ISIN записано %d, вид записан %d",
        updated,
        len(lots),
        linked,
        kinds,
    )
    return updated


async def _sync_security_kinds(
    client: IssClient, repository: MarketDataRepository, aliases: dict[str, str]
) -> int:
    """Проставить вид бумаги: акция или пай фонда (FR-060).

    С 22.06.2026 фонды торгуются на доске акций, и сбор получает их вместе.
    Вид берётся у биржи, догадки нет. Нераспознанный вид бумаги, торговавшейся
    в последней собранной сессии, — отказ справочника с названием бумаги: она
    кандидат, и догадка молча отправила бы фонд во вход модели. У снятой с
    торгов — запись в журнале: из набора она выходит сама (FR-060f), а вечный
    отказ из-за неё повторял бы справочник весь день, каждый день.
    """
    codes = await client.fetch_equity_security_types()
    kinds: dict[str, str] = {}
    for ticker, code in codes.items():
        kind = SECTYPE_KIND.get(code)
        if kind is not None:
            kinds[aliases.get(ticker, asset_id_for(ticker))] = kind
    # Незнакомый код не решается здесь: если бумага есть в хранилище, её вид
    # останется пустым и будет спрошен описанием ниже.
    written = await repository.update_security_kinds(kinds)

    # Бумаги с историей, которых в списке доски уже нет: их вид — из описания.
    unknown: dict[str, str] = {}
    for asset_id, ticker in (await repository.assets_without_kind()).items():
        try:
            group = await client.fetch_security_group(ticker)
        except ResponseContractError as error:
            # Сломанное описание одной бумаги не обрывает разбор остальных:
            # недоступность биржи по-прежнему роняет весь справочник.
            unknown[asset_id] = f"{ticker} ({error})"
            continue
        kind = GROUP_KIND.get(group or "")
        if kind is None:
            unknown[asset_id] = f"{ticker} (GROUP {group or 'нет'})"
            continue
        written += await repository.update_security_kinds({asset_id: kind})

    if unknown:
        latest = await repository.latest_bar_session()
        current = (
            set(await repository.assets_without_kind(on=latest)) & set(unknown)
            if latest is not None
            else set()
        )
        if current:
            raise ResponseContractError(
                "вид бумаги не распознан: " + ", ".join(sorted(unknown[a] for a in current)[:10])
            )
        logger.warning(
            "справочник бумаг: вид снятых с торгов бумаг не распознан, из набора они "
            "исключаются (FR-060f): %s",
            ", ".join(sorted(unknown.values())[:10]),
        )
    return written
