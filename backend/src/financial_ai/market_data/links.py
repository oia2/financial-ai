"""Связь бумаги и фьючерса во времени.

До этой фичи соответствие строилось из ISS на каждый прогон и жило в памяти
процесса. Ответить «с какой даты у бумаги есть фьючерс» было нельзя, смена
семейства контрактов проходила бесследно, а ключом был тикер — то есть имя
бумаги на период, а не сама бумага.

Здесь связь становится историей, и держится она на четырёх решениях.

**Бумага выбирается по базовому активу, эмитент — проверка.** Идентификатора
базовой бумаги в описании контракта нет вовсе; `EMITTER_ID` есть и совпадает,
но одного его мало: `SBER` и `SBERP` по нему неразличимы. Поэтому выбор — по
`underlying_asset` из списка серий, а эмитент подтверждает выбор (сверено
2026-09-17, см. `PROVENANCE.md`).

**Промах сопоставления — неуспех, а не «фьючерса нет».** Бумага, по которой
позиции собирались, не может «просто перестать сопоставляться»: такое молчание
выглядело бы нормой и стоило бы ряда (FR-020a).

**Смена семейства контрактов — событие.** Выбор по открытому интересу может
переключиться между сериями, и тихая подмена склеила бы ряд из двух разных
инструментов. Прежний интервал закрывается, новый открывается, смена
называется человеку (FR-016).

**Переименование — не новая бумага.** Тикер меняется, ISIN нет. Сущность
остаётся прежней, новое имя записывается псевдонимом, история не рвётся
(FR-038).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import asset_id_for
from financial_ai.market_data.sources.positions_client import CONTRACT_SUFFIX

logger = logging.getLogger(__name__)

# Чем подтверждён выбор контракта. Пишется в связь: через год «почему у этой
# бумаги такой контракт» — вопрос к данным, а не к памяти.
BY_UNDERLYING_AND_EMITTER = "underlying_and_emitter"
BY_UNDERLYING = "underlying_only"

OPENED = "opened"
CHANGED = "changed"
CLOSED = "closed"
RENAMED = "renamed"


class LinkMismatchError(RuntimeError):
    """Сопоставление бумаги и контракта не подтвердилось.

    Отдельный тип, потому что это неуспех источника с причиной, а не отсутствие
    инструмента: разница между «фьючерса нет» и «мы его потеряли» — разница
    между нормой и дефектом (FR-020a).
    """


@dataclass(frozen=True, slots=True)
class LinkEvent:
    """Изменение состава инструментов, которое видно человеку."""

    kind: str
    asset_id: str
    ticker: str
    contract_code: str | None
    detail: str

    def describe(self) -> str:
        return f"{self.ticker}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Чем предлагается связать бумагу на эту дату."""

    contract_code: str
    open_interest: int
    # Серия, по которой проверяется эмитент. Описание запрашивается по ней, а
    # не по семейству: у семейства своего описания нет.
    probe_secid: str


async def build_candidates(iss: IssClient) -> dict[str, Candidate]:
    """Соответствие «тикер акции → семейство контрактов» по данным ISS.

    Когда у бумаги несколько семейств (классическое и вечное, обычное и мини),
    берётся то, где больше суммарный открытый интерес: позиции живут там, где
    торгуют. При равном интересе выбор не зависит от порядка ответа биржи —
    иначе он бы молча менялся от прогона к прогону.
    """
    series = await iss.fetch_futures_series()
    open_interest = await iss.fetch_futures_open_interest()

    by_underlying: dict[str, dict[str, set[str]]] = {}
    for row in series:
        underlying = row.get("underlying_asset")
        asset_code = row.get("asset_code")
        secid = row.get("secid")
        if not isinstance(underlying, str) or not isinstance(asset_code, str):
            continue
        if not underlying.strip() or not asset_code.strip():
            continue
        family = asset_code.strip().upper()
        probe = secid.strip().upper() if isinstance(secid, str) and secid.strip() else family
        by_underlying.setdefault(underlying.strip().upper(), {}).setdefault(family, set()).add(
            probe
        )

    candidates: dict[str, Candidate] = {}
    for ticker, families in by_underlying.items():
        best = max(sorted(families), key=lambda family: open_interest.get(family, 0))
        candidates[ticker] = Candidate(
            contract_code=f"{best}{CONTRACT_SUFFIX}",
            open_interest=open_interest.get(best, 0),
            # Серия выбирается наименьшей по коду, а не первой в ответе: порядок
            # строк у биржи не гарантирован, и проверка эмитента не должна
            # зависеть от него.
            probe_secid=min(families[best]),
        )

    logger.info("кандидаты связи: %d базовых активов", len(candidates))
    return candidates


async def sync_aliases(
    repository: MarketDataRepository,
    iss: IssClient,
    session_date: dt.date,
) -> list[LinkEvent]:
    """Опознать бумаги по ISIN и записать переименования.

    Новый тикер при известном ISIN — переименование, а не появление бумаги.
    Сущность остаётся прежней: наблюдения продолжают ложиться в тот же ряд, а
    новое имя действует с этой сессии.
    """
    isins = await iss.fetch_equity_isins()
    if not isins:
        return []

    events: list[LinkEvent] = []
    known = await repository.tickers_with_history()

    for ticker, isin in sorted(isins.items()):
        existing = await repository.asset_by_isin(isin)
        asset_id = asset_id_for(ticker)

        if existing is None or existing == asset_id:
            # Бумага новая либо уже под своим именем: псевдоним всё равно
            # записывается, иначе переименование назад некуда будет отнести.
            await repository.upsert_alias(ticker, asset_id, session_date)
            continue

        previous = existing.removeprefix("EQ_AST_")
        await repository.close_alias(previous, session_date - dt.timedelta(days=1))
        await repository.upsert_alias(ticker, existing, session_date)
        if previous in known:
            events.append(
                LinkEvent(
                    kind=RENAMED,
                    asset_id=existing,
                    ticker=ticker,
                    contract_code=None,
                    detail=f"переименована из {previous}, история сохранена",
                )
            )

    # Ключ здесь — сущность, а не имя: переименованная бумага должна получить
    # свой ISIN под прежним идентификатором, иначе якорь не поставится вовсе.
    aliases = await repository.aliases_on(session_date)
    await repository.update_isins(
        {aliases.get(ticker, asset_id_for(ticker)): isin for ticker, isin in isins.items()}
    )
    return events


async def sync_links(
    repository: MarketDataRepository,
    iss: IssClient,
    session_date: dt.date,
    *,
    verify_emitter: bool = True,
) -> list[LinkEvent]:
    """Привести связи в соответствие с составом инструментов на эту сессию.

    Эмитент проверяется только у новых и сменившихся связей: продление
    действующей ничего не меняет, а описание стоит обращения на инструмент.
    Цена сверки таким образом пропорциональна изменению состава, а не размеру
    доски.
    """
    events = list(await sync_aliases(repository, iss, session_date))
    candidates = await build_candidates(iss)

    traded = await repository.assets_traded_on(session_date)
    active = await repository.active_links_on(session_date)
    aliases = await repository.aliases_on(session_date)

    emitters: dict[str, str | None] = {}

    for ticker, candidate in sorted(candidates.items()):
        asset_id = aliases.get(ticker, asset_id_for(ticker))
        if asset_id not in traded and asset_id not in active:
            # Контракт есть, а бумаги на доске нет: связывать нечего.
            continue

        if active.get(asset_id) == candidate.contract_code:
            continue

        chosen_by = BY_UNDERLYING
        if verify_emitter:
            agree = await _emitters_agree(iss, emitters, ticker, candidate.probe_secid)
            if agree is True:
                chosen_by = BY_UNDERLYING_AND_EMITTER
            elif agree is False and asset_id in active:
                # Бумага со связью потеряла подтверждение: это неуспех
                # источника с причиной, а не «фьючерса нет» (FR-020a).
                raise LinkMismatchError(
                    f"{ticker}: эмитент контракта {candidate.contract_code} не совпал с эмитентом "
                    "бумаги, связь не подтверждена"
                )
            elif agree is False:
                # Связи ещё не было, и ставить неподтверждённую нельзя: ряд
                # склеился бы из двух разных инструментов.
                logger.warning(
                    "связь %s → %s отклонена: эмитенты разные",
                    ticker,
                    candidate.contract_code,
                )
                continue

        changed = await repository.open_link(
            asset_id=asset_id,
            contract_code=candidate.contract_code,
            valid_from=session_date,
            chosen_by=chosen_by,
            open_interest=candidate.open_interest,
        )
        events.append(
            LinkEvent(
                kind=CHANGED if changed else OPENED,
                asset_id=asset_id,
                ticker=ticker,
                contract_code=candidate.contract_code,
                detail=(
                    f"контракт сменился на {candidate.contract_code}"
                    if changed
                    else f"появился фьючерс {candidate.contract_code}"
                ),
            )
        )

    events.extend(await _close_disappeared(repository, candidates, active, aliases, session_date))
    return events


async def _close_disappeared(
    repository: MarketDataRepository,
    candidates: dict[str, Candidate],
    active: dict[str, str],
    aliases: dict[str, str],
    session_date: dt.date,
) -> list[LinkEvent]:
    """Закрыть связи бумаг, у которых контракта больше нет.

    Закрытие — тоже событие: «позиций стало меньше» без объяснения выглядит как
    пропуск сбора, хотя инструмента просто не стало.
    """
    still_linked = {
        aliases.get(ticker, asset_id_for(ticker)): candidate.contract_code
        for ticker, candidate in candidates.items()
    }

    events: list[LinkEvent] = []
    for asset_id, contract_code in sorted(active.items()):
        if asset_id in still_linked:
            continue
        await repository.close_link(asset_id, session_date - dt.timedelta(days=1))
        events.append(
            LinkEvent(
                kind=CLOSED,
                asset_id=asset_id,
                ticker=asset_id.removeprefix("EQ_AST_"),
                contract_code=contract_code,
                detail=f"контракта {contract_code} больше нет в списке серий",
            )
        )
    return events


async def _emitters_agree(
    iss: IssClient,
    cache: dict[str, str | None],
    ticker: str,
    probe_secid: str,
) -> bool | None:
    """Совпадает ли эмитент бумаги с эмитентом контракта.

    Три ответа, а не два. ``None`` — источник не дал поля хотя бы у одной
    стороны: это отсутствие подтверждения, а не расхождение, и выдавать его за
    промах сопоставления нельзя.
    """
    for secid in (ticker, probe_secid):
        if secid not in cache:
            cache[secid] = await iss.fetch_emitter_id(secid)

    asset_emitter = cache[ticker]
    contract_emitter = cache[probe_secid]
    if asset_emitter is None or contract_emitter is None:
        return None
    return asset_emitter == contract_emitter


__all__ = [
    "BY_UNDERLYING",
    "BY_UNDERLYING_AND_EMITTER",
    "CHANGED",
    "CLOSED",
    "OPENED",
    "RENAMED",
    "Candidate",
    "LinkEvent",
    "LinkMismatchError",
    "build_candidates",
    "sync_aliases",
    "sync_links",
]
