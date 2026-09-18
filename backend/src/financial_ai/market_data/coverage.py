"""Сводка состояния данных по группам источников.

Отвечает на вопрос «что у нас есть» так, чтобы его не приходилось задавать
запросами к таблицам. Именно отсутствие такого отчёта позволило трём дефектам
из четырёх прожить незамеченными.

**Два числа, а не одно.** По каждой группе считаются и покрытие в сессиях, и
доля строк со значениями. Дефект позиций жил ровно в зазоре между ними:
покрытие 224 сессии из 224, значений 5 из 57 029. Отчёт, показывающий только
первое, объявил бы группу собранной.

Конкретных значений наблюдений здесь нет и быть не должно: это отчёт о полноте,
а не просмотр данных.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, groups, plan
from financial_ai.market_data.calendar import TradingCalendar, moscow_today
from financial_ai.market_data.repository import MarketDataRepository


@dataclass(frozen=True, slots=True)
class GroupCoverage:
    """Состояние одной группы."""

    group_id: str
    title: str
    has_history: bool

    window_sessions: int | None
    sessions_covered: int | None
    period_from: dt.date | None
    period_till: dt.date | None
    gaps: int | None

    rows_total: int
    rows_with_values: int

    # Исход каждого источника группы. Полнота считается по каждому, а не по
    # любому: успех одного не закрывает пропуск другого (FR-032).
    sources: list[dict[str, object]] = field(default_factory=list)

    @property
    def coverage_ratio(self) -> float | None:
        if not self.has_history or not self.window_sessions:
            return None
        return round((self.sessions_covered or 0) / self.window_sessions, 4)

    @property
    def value_ratio(self) -> float | None:
        """Доля строк со значениями.

        ``None`` при отсутствии строк: ноль означал бы «всё пусто», а нечему
        быть пустым.
        """
        if not self.rows_total:
            return None
        return round(self.rows_with_values / self.rows_total, 4)

    @property
    def looks_collected_but_empty(self) -> bool:
        """Покрытие есть, значений нет.

        Признак того самого дефекта: группа выглядит собранной и пуста. Ради
        него отчёт и заводится.
        """
        coverage = self.coverage_ratio
        values = self.value_ratio
        return coverage is not None and coverage > 0 and values is not None and values < 0.01

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "group": self.group_id,
            "title": self.title,
            "has_history": self.has_history,
            "rows_total": self.rows_total,
            "rows_with_values": self.rows_with_values,
            "value_ratio": self.value_ratio,
            # Вывод делает ответ, а не тот, кто его читает. Иначе порог
            # пришлось бы повторить в команде и ещё раз в интерфейсе, и при
            # первом же уточнении они разошлись бы (FR-013a).
            "looks_collected_but_empty": self.looks_collected_but_empty,
            "sources": self.sources,
        }
        if self.has_history:
            # У справочника этих полей НЕТ вовсе, а не нули: ноль читался бы
            # как «ничего не собрано».
            payload |= {
                "window_sessions": self.window_sessions,
                "sessions_covered": self.sessions_covered,
                "coverage_ratio": self.coverage_ratio,
                "period_from": self.period_from.isoformat() if self.period_from else None,
                "period_till": self.period_till.isoformat() if self.period_till else None,
                "gaps": self.gaps,
            }
        return payload


# Сколько неудач источника показывать. Их бывает больше, но список — не
# журнал: человеку нужно увидеть, что именно и когда сломалось, а не пролистать
# триста строк.
FAILURES_SHOWN = 20


async def _last_collected_session(
    repository: MarketDataRepository,
    calendar: TradingCalendar,
    asof_date: dt.date,
    depth: int = 10,
) -> dt.date | None:
    """Последняя сессия не позже даты сводки, за которую собраны котировки.

    Глубина ограничена: если котировок нет и за десять сессий подряд, состав
    неизвестен, и честнее сказать это, чем уйти перебором в начало истории.
    """
    window = await calendar.window(asof_date, depth)
    for day in reversed(window):
        if await repository.assets_traded_on(day):
            return day
    return None


async def _source_outcomes(
    repository: MarketDataRepository,
    group: groups.SourceGroup,
    window: list[dt.date],
) -> list[dict[str, object]]:
    """Исход каждого источника группы за окно.

    Нужен, чтобы неполнота группы объяснялась именем источника, а не оставалась
    числом. У «глобальных рядов» четыре источника, и ошибка одного из них — это
    ошибка конкретного ряда, а не группы вообще.

    У группы без оси сессий окна нет, но источники есть, и молчать о них нельзя:
    пустой список читался бы как «источников ноль». Их исход берётся по
    последнему успешному прогону — для справочника это и есть весь его ответ.
    """
    # Неудачи по дням — один запрос на группу, а не на источник. Берётся
    # ПОСЛЕДНИЙ прогон каждой пары «сессия — источник» и только неуспешный:
    # иначе удачный повтор не снимал бы отметку, и перечень превратился бы в
    # журнал былых неудач.
    failures: dict[str, list[dict[str, object]]] = {}
    for run in await repository.failed_runs_for_sessions(window):
        if run.session_date is None:
            continue
        failures.setdefault(run.source_id, []).append(
            {
                "session_date": run.session_date.isoformat(),
                "reason": run.failure_reason,
            }
        )

    outcomes: list[dict[str, object]] = []
    for source_id in group.source_ids:
        scope = next(
            (spec.scope for spec in plan.CATCHUP_PLAN if spec.source_id == source_id),
            plan.SESSION,
        )
        broken = failures.get(source_id, [])

        if window:
            covered = await repository.sessions_with_successful_run(window, source_id)
            count = len(covered)
            # Три состояния, а не два. «Не всё покрыто» и «источник падал» —
            # разные вещи: первое бывает на любом недособранном окне и ничего
            # не требует, второе требует вмешательства. Пока состояний было
            # два, экран называл ошибкой всякую неполноту и не мог показать
            # причину, потому что причины не было.
            if count >= len(window):
                status = "ok"
            elif broken:
                status = "failed"
            else:
                status = "partial"
        else:
            status = "ok" if await repository.last_successful_run_at(source_id) else "failed"
            count = 0

        outcomes.append(
            {
                "source_id": source_id,
                "title": plan.title_of(source_id),
                "scope": scope,
                "status": status,
                "sessions_covered": count,
                # Свежие сверху: «источник с ошибкой» без дня и причины — это
                # состояние, с которым человеку нечего делать.
                "failures": sorted(
                    broken,
                    key=lambda row: str(row["session_date"]),
                    reverse=True,
                )[:FAILURES_SHOWN],
                # Сколько их всего: список ограничен, и молчать об остатке
                # нельзя — иначе двадцатая строка выглядит последней.
                "failures_total": len(broken),
            }
        )
    return outcomes


async def build_report(
    session: AsyncSession, settings: Settings, asof_date: dt.date
) -> dict[str, object]:
    """Собрать сводку по всем группам на дату решения."""
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    # Состав бумаг. Знаменатель — бумаги с котировкой за сессию, а не все,
    # когда-либо встречавшиеся в данных: тот счёт только растёт и медленно
    # врёт, потому что ушедшая с торгов бумага остаётся в нём навсегда
    # (FR-013, FR-037).
    #
    # Считается он по последней УСПЕШНО собранной сессии, а не по дате сводки.
    # Признак торгуемости выводится из наблюдений, поэтому «не торговалась» и
    # «не собрали» по данным неразличимы: на несобранной дате состав вышел бы
    # нулевым, и несобранная сессия выглядела бы отсутствием торгов (FR-019a).
    universe_date = await _last_collected_session(repository, calendar, asof_date)
    traded = await repository.assets_traded_on(universe_date) if universe_date else set()
    links = await repository.active_links_on(universe_date) if universe_date else {}

    rows: list[GroupCoverage] = []
    pending: set[dt.date] = set()
    for group in groups.GROUPS:
        window_size = group.window_sessions(settings)
        window = await calendar.window(asof_date, window_size) if window_size else []

        missing: list[dt.date] = []
        if window:
            # Те же недостающие сессии, что найдёт сбор: правило полноты одно
            # на сводку, поиск пропусков и решение о работе (FR-032).
            missing = await completeness.missing_sessions(repository, group, window)
            pending.update(missing)

        raw = await repository.group_coverage(
            group.model,
            group.session_column,
            group.value_columns,
            window or None,
        )

        sources = await _source_outcomes(repository, group, window)

        rows.append(
            GroupCoverage(
                group_id=group.group_id.value,
                title=group.title,
                has_history=group.has_history,
                window_sessions=len(window) if group.has_history else None,
                # Покрытие группы считается ТЕМ ЖЕ правилом, что и исход
                # каждого её источника: сессия закрыта, если есть непустое
                # наблюдение либо успешный прогон. Прежде строка группы шла от
                # наблюдений, а раскрытие — от исходов прогонов, и на экране
                # рядом стояли два числа об одном и том же: «11 из 82» сверху и
                # «13 из 82» внутри (FR-032).
                sessions_covered=(len(window) - len(missing)) if group.has_history else None,
                period_from=raw.period_from,
                period_till=raw.period_till,
                gaps=len(missing) if group.has_history else None,
                rows_total=raw.rows_total,
                rows_with_values=raw.rows_with_values,
                sources=sources,
            )
        )

    # Окно догона — не окно группы: у групп они разные (314 сессий у котировок,
    # 82 у позиций), а планирование прогона считает своё. Форме запуска нужно
    # именно оно, и взять его она должна из того же источника, каким
    # пользуется планирование, а не выводить из строк сводки (FR-013b).
    catchup_window = await calendar.window(asof_date, settings.catchup_window_sessions)

    # Сессия, которую возьмёт следующий сбор. Именно она, а не последняя
    # сессия календаря: при отставании сбор берёт самую раннюю несобранную, и
    # обещать сегодняшнюю дату значило бы говорить неправду ровно тогда, когда
    # человек и смотрит на эту строку (FR-024a).
    #
    # Когда несобранного нет, следующей будет текущая сессия после порога —
    # ближайший известный торговый день. Будущих дат календарь не знает: он
    # строится по СОСТОЯВШИМСЯ торгам.
    next_session = min(pending) if pending else await calendar.latest_session(moscow_today())

    return {
        "asof_date": asof_date.isoformat(),
        "universe": {
            "assets": len(traded),
            "assets_with_futures": len(traded & set(links)),
            # Дата, по которой посчитан состав: она может быть старше даты
            # сводки, и молчать об этом нельзя.
            "asof_date": universe_date.isoformat() if universe_date else None,
        },
        "next_session": next_session.isoformat() if next_session else None,
        # Порог сбора текущей сессии. Биржевое время отдаёт сервер: оно живёт в
        # настройке сборщика, и второе объявление того же факта в интерфейсе
        # однажды разошлось бы с первым.
        "ingest_after_close": settings.market_data_ingest_after_close,
        "catchup_window": {
            "date_from": catchup_window[0].isoformat() if catchup_window else None,
            "date_till": catchup_window[-1].isoformat() if catchup_window else None,
            "sessions": len(catchup_window),
        },
        "groups": [row.to_dict() for row in rows],
    }
