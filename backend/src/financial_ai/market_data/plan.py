"""План сбора: какие источники и в каком порядке идут за данными.

Порядок здесь не «справочный», а **тот же самый**, которым идёт сбор: против
`ingest.ingest_session` и `ingest._catch_up_session` стоит проверка, и разойтись
они не могут молча. Иначе экран показывал бы план, которого не существует.

**Область источника важнее его имени.** Три разных:

- `session` — идёт на каждую сессию;
- `period` — один запрос на весь период догона независимо от его длины;
- `daily` — раз в сутки, к сессии не привязан (торговый календарь).

Счётчик «источники сессии» считает только первые. Иначе он обещал бы, что
диапазонные и суточные повторятся на следующий день, а они не повторятся
(spec 008, FR-007).
"""

from __future__ import annotations

from dataclasses import dataclass

SESSION = "session"
PERIOD = "period"
DAILY = "daily"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Источник в плане: чем он назван человеку и когда выполняется."""

    source_id: str
    title: str
    scope: str


# Ежедневный прогон. Календарь идёт первым: пока он не ответил, неизвестно,
# была ли сессия вообще. Дивидендов здесь больше нет — их никто не читает, а
# прогон платил за них обращением к брокеру по каждой бумаге (FR-008).
DAILY_PLAN: tuple[SourceSpec, ...] = (
    SourceSpec("trading_calendar", "Торговый календарь", DAILY),
    SourceSpec("equity_d1", "Котировки акций", SESSION),
    SourceSpec("equity_agg", "Агрегаты торгов", SESSION),
    SourceSpec("global_series", "Глобальные ряды", SESSION),
    SourceSpec("equity_sectors", "Секторы бумаг", SESSION),
    SourceSpec("index_constituents", "Состав индекса", SESSION),
    SourceSpec("equity_lot_sizes", "Лоты бумаг", SESSION),
    SourceSpec("brent", "Brent", SESSION),
    SourceSpec("cbr", "Курсы и ставка ЦБ", SESSION),
    SourceSpec("futures_positions", "Позиции по фьючерсам", SESSION),
)

# Ручной сбор истории. План короче и составной: диапазонные источники берут
# весь период одним запросом, остальные идут по сессиям.
CATCHUP_PLAN: tuple[SourceSpec, ...] = (
    SourceSpec("global_series", "Глобальные ряды", PERIOD),
    SourceSpec("cbr", "Курсы и ставка ЦБ", PERIOD),
    SourceSpec("equity_d1", "Котировки акций", SESSION),
    SourceSpec("equity_agg", "Агрегаты торгов", SESSION),
    SourceSpec("index_constituents", "Состав индекса", SESSION),
    SourceSpec("brent", "Brent", SESSION),
    SourceSpec("futures_positions", "Позиции по фьючерсам", SESSION),
)

MODE_DAILY = "daily"
MODE_MANUAL = "manual"


def for_mode(mode: str) -> tuple[SourceSpec, ...]:
    """План выбранного режима."""
    return CATCHUP_PLAN if mode == MODE_MANUAL else DAILY_PLAN


def title_of(source_id: str) -> str:
    """Имя источника для человека. Неизвестный показывается как есть."""
    for spec in (*DAILY_PLAN, *CATCHUP_PLAN):
        if spec.source_id == source_id:
            return spec.title
    return source_id
