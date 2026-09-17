"""Позиции физических и юридических лиц по фьючерсам.

Самая тонкая из модальностей, и не из-за объёма кода.

**Задержанное прибытие.** Данные приходят позже закрытия сессии и годятся для
снимка на `asof_date = t`, только если успели до формирования набора. Если не
успели — они остаются наблюдением о дне `t`.

**Передатирование запрещено.** Опоздавший файл нельзя записать как наблюдение
о следующей сессии. Такая ошибка не падает тестом и не видна в данных: она
просто сдвигает историю на день, и модель обучается на смещённом сигнале.
Здесь запрет держится ещё и на сверке даты ответа с запрошенной: биржа отдаёт
последний доступный снимок, если за дату данных нет.

**Отсутствие — не ноль.** Покрытие частичное по своей природе: позиции есть не
по всем активам. Ноль означал бы «участники не держат позиций», а пропуск —
«мы не знаем». Для модели это разные утверждения.

**Пустота — не частичность.** Полное отсутствие значений записывается как
неуспех с причиной (FR-018). Прежняя реализация писала 260 строк пустых
значений на сессию и сообщала «получено 260, записано 260 (покрытие частичное —
это норма)»; формулировка верна для настоящей частичности и именно поэтому
скрыла дефект, при котором значений не было вообще: 5 непустых на 57 029.

Источник — не биржевой интерфейс данных, а форма на сайте биржи: см.
`positions_client.py`. Запись о происхождении в `PROVENANCE.md` до этой фичи
утверждала обратное.
"""

from __future__ import annotations

import datetime as dt
import logging

from financial_ai.market_data.repository import MarketDataRepository, PositionRow
from financial_ai.market_data.sources.equity_d1 import asset_id_for
from financial_ai.market_data.sources.positions_client import (
    PositionsClient,
    PositionsSourceError,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "futures_positions"


class EmptyPositionsError(RuntimeError):
    """Ни одного значения там, где источник обязан их приносить.

    Отдельный тип, а не возврат нуля: прогон должен записаться как неуспех с
    причиной, а не как успех с нулём строк (FR-018).
    """


async def sync_positions(
    client: PositionsClient,
    repository: MarketDataRepository,
    session_date: dt.date,
    contracts: dict[str, str],
    sessions: list[dt.date] | None = None,
) -> int:
    """Собрать позиции за одну торговую сессию.

    Чем спрашивать — решает действующая связь бумаги: она знает, с какой даты
    контракт у бумаги есть, и не переключается между семействами молча.
    ``contracts`` остаётся запасным соответствием на первый прогон, когда связей
    ещё нет вовсе; дальше оно не используется.

    Три правила аккуратности выполняются здесь, а не в клиенте, потому что все
    три требуют знания уже собранного:

    - собранная пара «инструмент — дата» повторно не запрашивается (FR-024c);
    - периоды до первой доступной даты инструмента не запрашиваются (FR-024b);
    - акция без контракта не запрашивается вовсе (FR-022).
    """
    # Применимость определяется действующей связью, а не построенным на лету
    # соответствием: связь знает, С КАКОЙ ДАТЫ контракт у бумаги есть, и не
    # переключается между сериями молча (FR-017, FR-039).
    links = await repository.active_links_on(session_date)
    if not links:
        links = {asset_id_for(ticker): code for ticker, code in contracts.items()}
    if not links:
        raise EmptyPositionsError("действующих связей бумаг и контрактов нет: спрашивать нечего")

    known_tickers = await repository.tickers_with_history()
    already = await repository.assets_with_positions(session_date)
    first_seen = await repository.first_position_dates()

    # Бумага, по которой позиции собирались, обязана иметь связь. Её пропажа —
    # неуспех с причиной, а не «фьючерса нет»: второе выглядит нормой (FR-020a).
    lost = sorted(asset_id for asset_id in first_seen if asset_id not in links)
    if lost:
        raise EmptyPositionsError(
            "бумаги с историей позиций потеряли связь с контрактом: "
            + ", ".join(asset_id.removeprefix("EQ_AST_") for asset_id in lost)
        )

    wanted = sorted(ticker for ticker in known_tickers if asset_id_for(ticker) in links)
    if not wanted:
        raise EmptyPositionsError(
            "ни у одной известной бумаги нет фьючерсного контракта: соответствие не построилось"
        )

    rows: list[PositionRow] = []
    requested = 0
    skipped_collected = 0
    skipped_early = 0

    for ticker in wanted:
        asset_id = asset_id_for(ticker)

        if asset_id in already:
            # Пара уже собрана. Для источников с единицей «дата» это следует из
            # правил догона; здесь единица мельче сессии, и правило нужно явно.
            skipped_collected += 1
            continue

        contract = links[asset_id]
        existed = await _existed_then(
            client, contract, session_date, first_seen.get(asset_id), sessions
        )
        if not existed:
            # Инструмента тогда ещё не существовало: обращение заведомо не
            # принесёт данных, а такие запрещены (FR-022).
            skipped_early += 1
            continue

        requested += 1
        snapshot = await client.fetch(contract, session_date)
        if snapshot is None:
            continue

        rows.append(
            PositionRow(
                asset_id=asset_id,
                session_date=session_date,
                # Контракт входит в ключ наблюдения: повторный сбор той же даты
                # другим семейством не должен затирать прежнее молча (FR-039).
                contract_code=contract,
                fiz_long=snapshot.fiz_long,
                fiz_short=snapshot.fiz_short,
                jur_long=snapshot.jur_long,
                jur_short=snapshot.jur_short,
            )
        )

    filled = [row for row in rows if _has_values(row)]

    if requested and not filled:
        # Ровно то различие, ради которого FR-018 существует: настоящая
        # частичность даёт значения хотя бы по части инструментов, дефект —
        # ноль. Ноль записывается как неуспех, а не как успешная пустота.
        raise EmptyPositionsError(
            f"позиции за {session_date}: выполнено {requested} обращений, "
            "ни одного значения не получено"
        )

    written = await repository.upsert_positions(filled)
    logger.info(
        "позиции за %s: спрошено %d, со значениями %d, записано %d "
        "(уже собрано %d, до появления инструмента %d)",
        session_date,
        requested,
        len(filled),
        written,
        skipped_collected,
        skipped_early,
    )
    return written


async def _existed_then(
    client: PositionsClient,
    contract: str,
    session_date: dt.date,
    known_since: dt.date | None,
    sessions: list[dt.date] | None,
) -> bool:
    """Существовал ли инструмент на эту сессию.

    **Отметка из собранных данных — нижняя граница, а не ответ.** Самая ранняя
    собранная сессия доказывает, что инструмент тогда существовал, и только: про
    более раннее время она не говорит ничего. Прежняя версия трактовала её как
    первую доступную дату и потому запрещала догон истории навсегда — сессии
    старше уже собранных пропускались как несуществующие. На стенде это
    выглядело так: догон за 2026-08-10…14 пропустил 69 активов из 69 при
    нулевых обращениях, хотя позиции по ним собраны с 2026-08-24.

    Поэтому отметка только **сокращает поиск**: сессия не старше её принимается
    без проб, а для более ранних поиск выполняется.
    """
    if known_since is not None and session_date >= known_since:
        return True

    first_available = await client.first_available_date(contract, sessions or [])
    if first_available is not None:
        return session_date >= first_available

    # Поиск выполнялся и не нашёл ничего — обращения по контракту бесполезны.
    # Если же поиска не было (сессий не передали), пропускать нельзя: незнание
    # не является основанием не спрашивать.
    return not client.searched_for_first_date(contract)


def _has_values(row: PositionRow) -> bool:
    """Есть ли в строке хоть одно значение.

    Пустые строки не записываются вовсе: именно они заставляли догон считать
    сессию закрытой, и правильные позиции за неё не появились бы никогда.
    """
    return any(
        value is not None for value in (row.fiz_long, row.fiz_short, row.jur_long, row.jur_short)
    )


__all__ = ["SOURCE_ID", "EmptyPositionsError", "PositionsSourceError", "sync_positions"]
