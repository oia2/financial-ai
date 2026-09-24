"""План сбора: какие источники и в каком порядке идут за данными.

Порядок здесь не «справочный», а **тот же самый**, которым идёт сбор: против
`ingest.ingest_session` и `ingest._catch_up_session` стоит проверка, и разойтись
они не могут молча. Иначе экран показывал бы план, которого не существует.

**Область источника важнее его имени.** Три разных:

- `session` — идёт на каждую сессию;
- `period` — один запрос на весь период догона независимо от его длины;
- `daily` — раз в сутки, к сессии не привязан: торговый календарь и
  справочники текущего состояния (секторы, лоты).

Счётчик «источники сессии» считает только первые. Иначе он обещал бы, что
диапазонные и суточные повторятся на следующий день, а они не повторятся
(spec 008, FR-007).
"""

from __future__ import annotations

from dataclasses import dataclass

SESSION = "session"
PERIOD = "period"
DAILY = "daily"

# Почему работа не завершена (FR-033f). «Ошибка источника» показывается только
# при первой причине: остановка человеком, обрыв перезапуском и сбой нашей
# обработки источником не являются, и прежде экран их не различал.
FAILURE_SOURCE = "source"
FAILURE_STOPPED = "stopped"
FAILURE_INTERRUPTED = "interrupted"
FAILURE_INTERNAL = "internal"
# Задержанный источник ещё не опубликовал дату (FR-032h). Это ожидание, а не
# отказ: попыткой закрыть сессию не считается и «ошибкой источника» не является.
FAILURE_UNPUBLISHED = "unpublished"

OUTCOME_COLLECTED = "collected"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"
OUTCOME_PENDING = "pending"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Источник в плане: чем он назван человеку и когда выполняется."""

    source_id: str
    title: str
    scope: str


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    """Единый итог выбранных единиц работы одной сессии."""

    outcome: str
    interrupted: bool = False


def fold_session_outcome(
    selected_sources: frozenset[str], statuses: dict[str, str]
) -> SessionOutcome:
    """Свернуть исходы точного плана сессии без выделения особого источника."""
    if not selected_sources:
        return SessionOutcome(OUTCOME_SKIPPED)

    selected = [statuses.get(source_id, OUTCOME_PENDING) for source_id in selected_sources]
    interrupted = "stopped" in selected
    closed = sum(status == "ok" for status in selected)
    failed = sum(status == "failed" for status in selected)
    skipped = sum(status == "skipped" for status in selected)
    pending = sum(status in {OUTCOME_PENDING, "stopped"} for status in selected)

    if closed == len(selected):
        outcome = OUTCOME_COLLECTED
    elif closed:
        outcome = OUTCOME_PARTIAL
    elif pending:
        outcome = OUTCOME_PENDING if not failed and not skipped else OUTCOME_PARTIAL
    elif failed:
        outcome = OUTCOME_FAILED
    elif skipped == len(selected):
        outcome = OUTCOME_SKIPPED
    else:
        outcome = OUTCOME_PENDING
    return SessionOutcome(outcome, interrupted)


# Ежедневный прогон. Календарь идёт первым: пока он не ответил, неизвестно,
# была ли сессия вообще. Дивидендов здесь больше нет — их никто не читает, а
# прогон платил за них обращением к брокеру по каждой бумаге (FR-008).
DAILY_PLAN: tuple[SourceSpec, ...] = (
    SourceSpec("trading_calendar", "Торговый календарь", DAILY),
    SourceSpec("equity_d1", "Котировки доски TQBR", SESSION),
    SourceSpec("equity_agg", "Агрегаты доски TQBR", SESSION),
    SourceSpec("global_series", "Глобальные ряды", SESSION),
    SourceSpec("index_constituents", "Состав индекса", SESSION),
    SourceSpec("brent", "Brent", SESSION),
    SourceSpec("cbr", "Курсы и ставка ЦБ", SESSION),
    # Справочники текущего состояния — суточные, как и календарь. Оси сессий у
    # них нет: ответ один и тот же, каким бы днём его ни спросили, и при
    # отставании в восемьдесят сессий посессионный порядок платил за них сто
    # шестьдесят обращений, переписывая одни и те же строки (FR-055).
    SourceSpec("equity_sectors", "Секторы бумаг", DAILY),
    SourceSpec("equity_lot_sizes", "Лоты бумаг", DAILY),
    SourceSpec("futures_positions", "Позиции по фьючерсам", SESSION),
)

# Ручной сбор истории. План короче и составной: диапазонные источники берут
# весь период одним запросом, остальные идут по сессиям.
CATCHUP_PLAN: tuple[SourceSpec, ...] = (
    SourceSpec("global_series", "Глобальные ряды", PERIOD),
    SourceSpec("cbr", "Курсы и ставка ЦБ", PERIOD),
    SourceSpec("equity_d1", "Котировки доски TQBR", SESSION),
    SourceSpec("equity_agg", "Агрегаты доски TQBR", SESSION),
    SourceSpec("index_constituents", "Состав индекса", SESSION),
    SourceSpec("brent", "Brent", SESSION),
    SourceSpec("futures_positions", "Позиции по фьючерсам", SESSION),
    # Выбранные справочники обновляются один раз за запуск, независимо от
    # списка дат (FR-033g). Прежде их можно было выбрать, но план их не
    # исполнял: выбор одних справочников отвечал «собирать нечего».
    SourceSpec("equity_sectors", "Секторы бумаг", DAILY),
    SourceSpec("equity_lot_sizes", "Лоты бумаг", DAILY),
)

# Справочники текущего состояния: без оси сессий, раз за запуск.
REFERENCE_SOURCES = frozenset({"equity_sectors", "equity_lot_sizes"})

# Суточные источники: исход пишут с датой, но сессию не собирают (FR-056b).
DAILY_SOURCES = frozenset(spec.source_id for spec in DAILY_PLAN if spec.scope == DAILY)

MODE_DAILY = "daily"
MODE_MANUAL = "manual"


def for_mode(mode: str) -> tuple[SourceSpec, ...]:
    """План выбранного режима."""
    return CATCHUP_PLAN if mode == MODE_MANUAL else DAILY_PLAN


# Чем назван результат источника. Взято из артефакта Open Design: «243 бумаги»,
# «9 рядов», «2 индекса». Пусто там, где число ничего не добавляет — у Brent
# ряд один, у календаря и справочников счёт не о том.
UNITS: dict[str, tuple[str, str, str]] = {
    "equity_d1": ("бумага", "бумаги", "бумаг"),
    "equity_agg": ("бумага", "бумаги", "бумаг"),
    "global_series": ("ряд", "ряда", "рядов"),
    "cbr": ("ряд", "ряда", "рядов"),
    "index_constituents": ("бумага", "бумаги", "бумаг"),
    "futures_positions": ("фьючерс", "фьючерса", "фьючерсов"),
}


def plural(count: int, one: str, few: str, many: str) -> str:
    """Существительное в форме, согласованной с числом."""
    tail, hundred = count % 10, count % 100
    if tail == 1 and hundred != 11:
        return one
    if 2 <= tail <= 4 and not 12 <= hundred <= 14:
        return few
    return many


def describe(source_id: str, rows: int) -> str | None:
    """Чем закончился источник, словами и числом.

    Подпись собранного источника — единственное, что отличает идущую работу от
    замершей. Переносилось же только сообщение об ошибке: в обычном прогоне
    лента стояла без единой подписи, а текст появлялся ровно тогда, когда
    что-то ломалось (FR-058f).
    """
    unit = UNITS.get(source_id)
    if unit is None or rows <= 0:
        return None
    return f"{rows} {plural(rows, *unit)}"


def scope_of(source_id: str) -> str:
    """Область источника: ручной план первым — в нём диапазонные идут периодом."""
    for spec in (*CATCHUP_PLAN, *DAILY_PLAN):
        if spec.source_id == source_id:
            return spec.scope
    return SESSION


def title_of(source_id: str) -> str:
    """Имя источника для человека. Неизвестный показывается как есть."""
    for spec in (*DAILY_PLAN, *CATCHUP_PLAN):
        if spec.source_id == source_id:
            return spec.title
    return source_id
