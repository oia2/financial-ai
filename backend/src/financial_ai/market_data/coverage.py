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
from financial_ai.market_data.calendar import TradingCalendar, moscow_now
from financial_ai.market_data.models import KIND_FUND
from financial_ai.market_data.repository import (
    CURRENT_COVERAGE_VERSION,
    GroupCoverageRaw,
    MarketDataRepository,
    UnfinishedRun,
)


@dataclass(frozen=True, slots=True)
class GroupCoverage:
    """Состояние одной группы."""

    group_id: str
    title: str
    has_history: bool

    window_sessions: int | None
    sessions_covered: int | None
    requires_audit: int
    period_from: dt.date | None
    period_till: dt.date | None
    gaps: int | None

    rows_total: int
    rows_with_values: int

    # Исход каждого источника группы. Полнота считается по каждому, а не по
    # любому: успех одного не закрывает пропуск другого (FR-032).
    sources: list[dict[str, object]] = field(default_factory=list)

    # Идут ли строки группы во вход модели (FR-060c).
    model_input: bool = True

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
    def latest_failure(self) -> dict[str, object] | None:
        """Последняя причина незавершённой работы группы — одна строка факта.

        Берётся самая поздняя по дате сессии неудача среди недоказанных сессий
        всех источников, а для справочника — причина последнего отказа.
        """
        candidates: list[dict[str, object]] = []
        for source in self.sources:
            failures = source.get("failures")
            if isinstance(failures, list) and failures:
                candidates.append({**failures[0], "title": source["title"]})
            elif source.get("reason") and source.get("state") in {
                STATE_SOURCE_ERROR,
                STATE_INTERNAL_ERROR,
                STATE_INTERRUPTED,
            }:
                candidates.append(
                    {
                        "session_date": None,
                        "reason": source["reason"],
                        "kind": plan.FAILURE_SOURCE,
                        "title": source["title"],
                    }
                )
        if not candidates:
            return None
        return max(candidates, key=lambda item: str(item.get("session_date") or ""))

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
            "model_input": self.model_input,
            "rows_total": self.rows_total,
            "rows_with_values": self.rows_with_values,
            "value_ratio": self.value_ratio,
            # Вывод делает ответ, а не тот, кто его читает. Иначе порог
            # пришлось бы повторить в команде и ещё раз в интерфейсе, и при
            # первом же уточнении они разошлись бы (FR-013a).
            "looks_collected_but_empty": self.looks_collected_but_empty,
            "sources": self.sources,
            "requires_audit": self.requires_audit,
            # Состояние выбирает сервер, интерфейс только подписывает его: иначе
            # правило старшинства пришлось бы повторить в двух местах (FR-024e).
            "state": group_state(self.looks_collected_but_empty, self.sources),
            "latest_failure": self.latest_failure,
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
    closed_sources: dict[str, set[dt.date]] | None = None,
    audit_sources: dict[str, set[dt.date]] | None = None,
    boundary: dt.date | None = None,
    latest_runs: dict[tuple[dt.date, str], UnfinishedRun] | None = None,
) -> list[dict[str, object]]:
    """Исход каждого источника группы за окно.

    ``latest_runs`` — незавершённые последние исходы, прочитанные сводкой один раз на все
    группы; без него они читаются здесь.

    Нужен, чтобы неполнота группы объяснялась именем источника, а не оставалась
    числом. У «глобальных рядов» четыре источника, и ошибка одного из них — это
    ошибка конкретного ряда, а не группы вообще.

    У группы без оси сессий окна нет, но источники есть, и молчать о них нельзя:
    пустой список читался бы как «источников ноль». Для справочника различаются
    последний проверенный полный ответ, старый успех без доказательства и
    реальный отказ. Число строк само по себе ничего из этого не доказывает.
    """
    # Неудачи по дням — один запрос на группу, а не на источник. Берётся
    # ПОСЛЕДНИЙ прогон каждой пары «сессия — источник» и только неуспешный:
    # иначе удачный повтор не снимал бы отметку, и перечень превратился бы в
    # журнал былых неудач.
    #
    # Идущее обращение неудачей не является: прежде `running` попадал в тот же
    # перечень, и работающий сбор показывался «ошибкой источника» (FR-033f).
    failures: dict[str, list[dict[str, object]]] = {}
    running: dict[str, set[dt.date]] = {}
    if latest_runs is None:
        latest_runs = await repository.latest_unfinished_runs(window)
    in_window = set(window)
    for (day, source_id), run in latest_runs.items():
        if day not in in_window or source_id not in group.source_ids:
            continue
        if run.status == "running":
            running.setdefault(source_id, set()).add(day)
            continue
        if run.status not in {"failed", "stopped"}:
            continue
        failures.setdefault(source_id, []).append(
            {
                "session_date": day.isoformat(),
                "reason": run.failure_reason,
                "kind": _kind_of(run.status, run.failure_kind),
            }
        )

    outcomes: list[dict[str, object]] = []
    for source_id in group.source_ids:
        scope = plan.scope_of(source_id)
        broken = failures.get(source_id, [])
        state = STATE_MISSING
        audit: set[dt.date] = set()
        requires_audit = 0
        last_checked_at: str | None = None
        reason: str | None = None

        if window:
            # Тем же правилом, что и счёт группы, и ТЕМ ЖЕ расчётом: два числа
            # об одном и том же расходятся всегда, а посчитанные дважды стоят
            # втрое больше запросов на каждую отрисовку (FR-032).
            closed = (closed_sources or {}).get(source_id)
            if closed is None:
                closed = await completeness.closed_sessions(repository, group, source_id, window)
            count = len(closed)
            cached_audits = audit_sources or {}
            if source_id in cached_audits:
                audit = cached_audits[source_id]
            else:
                audit = await completeness.requires_audit_sessions(
                    repository,
                    group,
                    source_id,
                    window,
                    closed=closed,
                    boundary=boundary,
                )

            # Неудача за ЗАКРЫТУЮ сессию не показывается: данные получены
            # другим путём — диапазонный запрос приносит триста сессий одним
            # ответом, — и посессионные попытки после него падают, ничего не
            # меняя. Сводка говорила «собрано 314 из 314» и рядом «неудач по
            # дням 44»: оба утверждения верны по отдельности и противоречат
            # друг другу вместе (FR-058m).
            broken = [
                failure
                for failure in broken
                if dt.date.fromisoformat(str(failure["session_date"])) not in closed
            ]

            # Три состояния, а не два. «Не всё покрыто» и «источник падал» —
            # разные вещи: первое бывает на любом недособранном окне и ничего
            # не требует, второе требует вмешательства. Пока состояний было
            # два, экран называл ошибкой всякую неполноту и не мог показать
            # причину, потому что причины не было.
            if count >= len(window):
                state = STATE_COMPLETE
            elif running.get(source_id, set()) - closed:
                state = STATE_RUNNING
            else:
                state = _state_of_failures({str(item["kind"]) for item in broken})
        else:
            latest = await repository.latest_source_run(source_id)
            if latest is None:
                state = STATE_MISSING
            elif latest.status == "running":
                state = STATE_RUNNING
            elif latest.status != "ok":
                state = _state_of_failures({_kind_of(latest.status, latest.failure_kind)})
                reason = latest.failure_reason
            elif latest.coverage_version == CURRENT_COVERAGE_VERSION:
                state = STATE_COMPLETE
            else:
                state = STATE_MISSING
                requires_audit = 1
            if latest is not None:
                checked = latest.finished_at or latest.started_at
                last_checked_at = checked.isoformat()
            count = 0

        outcomes.append(
            {
                "source_id": source_id,
                "title": plan.title_of(source_id),
                "scope": scope,
                # Прежнее трёхзначное поле сохраняется для старых читателей:
                # «failed» — только отказ источника или сбой обработки.
                "status": _legacy_status(state),
                "state": state,
                "sessions_covered": count,
                "requires_audit": len(audit) if window else requires_audit,
                "last_checked_at": last_checked_at,
                "reason": reason,
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


# Состояние группы и источника (FR-024e). Перечень закрытый, порядок — порядок
# старшинства: у группы показывается первое, которое к ней относится.
STATE_EMPTY = "empty"
STATE_RUNNING = "running"
STATE_SOURCE_ERROR = "source_error"
STATE_INTERNAL_ERROR = "internal_error"
STATE_INTERRUPTED = "interrupted"
STATE_MISSING = "missing"
STATE_COMPLETE = "complete"
STATE_ORDER = (
    STATE_EMPTY,
    STATE_RUNNING,
    STATE_SOURCE_ERROR,
    STATE_INTERNAL_ERROR,
    STATE_INTERRUPTED,
    STATE_MISSING,
    STATE_COMPLETE,
)


def _kind_of(status: str, failure_kind: str | None) -> str:
    """Причина незавершённости записи; у старой записи без неё — по статусу."""
    if failure_kind:
        return failure_kind
    return plan.FAILURE_STOPPED if status == "stopped" else plan.FAILURE_SOURCE


def _state_of_failures(kinds: set[str]) -> str:
    """Состояние недоказанной работы по причинам её последних попыток."""
    if plan.FAILURE_SOURCE in kinds:
        return STATE_SOURCE_ERROR
    if plan.FAILURE_INTERNAL in kinds:
        return STATE_INTERNAL_ERROR
    if kinds & {plan.FAILURE_STOPPED, plan.FAILURE_INTERRUPTED}:
        return STATE_INTERRUPTED
    return STATE_MISSING


def _legacy_status(state: str) -> str:
    if state == STATE_COMPLETE:
        return "ok"
    if state in {STATE_SOURCE_ERROR, STATE_INTERNAL_ERROR}:
        return "failed"
    return "partial"


def group_state(looks_empty: bool, sources: list[dict[str, object]]) -> str:
    """Старшее состояние среди источников группы (FR-024e)."""
    if looks_empty:
        return STATE_EMPTY
    states = {str(source["state"]) for source in sources}
    return next((state for state in STATE_ORDER if state in states), STATE_COMPLETE)


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
    # Состав — по акциям: «фьючерс есть у N из M бумаг» относится к бумагам, для
    # которых фьючерсы бывают, а не к паям фондов (FR-060e).
    traded -= await repository.fund_asset_ids()
    links = await repository.active_links_on(universe_date) if universe_date else {}

    rows: list[GroupCoverage] = []
    pending: set[dt.date] = set()
    boundary = await repository.coverage_boundary()

    windows: dict[groups.GroupId, list[dt.date]] = {}
    for group in groups.GROUPS:
        window_size = group.window_sessions(settings)
        windows[group.group_id] = (
            group.trim(await calendar.window(asof_date, window_size)) if window_size else []
        )

    # Журнал исходов и доказательства читаются ОДИН раз на всю сводку, а не на
    # каждую группу. Журнал окна — тысячи записей, и повтор на каждую из шести
    # групп стоил секунды на каждое открытие раздела; у котировок и агрегатов по
    # две группы на один источник (FR-060a), и доказательства тех же дат
    # читались дважды.
    union = sorted({day for window in windows.values() for day in window})
    # Последний исход каждой пары выбирает база; наружу — только незавершённые.
    latest_runs = await repository.latest_unfinished_runs(union)
    closed_cache: dict[str, set[dt.date]] = {}
    rows_cache: dict[tuple[type, bool], dict[tuple[bool | None, dt.date], tuple[int, int]]] = {}

    async def raw_for(group: groups.SourceGroup, window: list[dt.date]) -> GroupCoverageRaw:
        """Числа строк группы в её окне из одного прохода по таблице (FR-060a)."""
        if not window or group.session_column is None:
            return await repository.group_coverage(
                group.model,
                group.session_column,
                group.value_columns,
                window or None,
                group.asset_kind,
            )
        split = group.asset_kind is not None
        key = (group.model, split)
        if key not in rows_cache:
            rows_cache[key] = await repository.rows_by_session(
                group.model, group.session_column, group.value_columns, union, split_by_kind=split
            )
        wanted = None if not split else group.asset_kind == KIND_FUND
        in_window = set(window)
        days: set[dt.date] = set()
        total = with_values = 0
        for (is_fund, day), (rows, filled) in rows_cache[key].items():
            if is_fund != wanted or day not in in_window:
                continue
            days.add(day)
            total += rows
            with_values += filled
        return GroupCoverageRaw(
            sessions_covered=len(days),
            period_from=min(days) if days else None,
            period_till=max(days) if days else None,
            rows_total=total,
            rows_with_values=with_values,
        )

    async def closed_for(
        group: groups.SourceGroup, window: list[dt.date]
    ) -> dict[str, set[dt.date]]:
        result: dict[str, set[dt.date]] = {}
        for source_id in group.source_ids:
            if source_id not in closed_cache:
                closed_cache[source_id] = await completeness.closed_sessions(
                    repository, group, source_id, union
                )
            result[source_id] = closed_cache[source_id] & set(window)
        return result

    for group in groups.GROUPS:
        window = windows[group.group_id]

        missing: list[dt.date] = []
        closed_sources: dict[str, set[dt.date]] = {}
        audit_sources: dict[str, set[dt.date]] = {}
        requires_audit: set[dt.date] = set()
        if window:
            # Те же недостающие сессии, что найдёт сбор: правило полноты одно
            # на сводку, поиск пропусков и решение о работе (FR-032). Считается
            # ОДИН раз на группу и отдаётся обоим потребителям.
            closed_sources = await closed_for(group, window)
            missing = await completeness.missing_sessions(repository, group, window, closed_sources)
            for source_id in group.source_ids:
                audit_sources[source_id] = await completeness.requires_audit_sessions(
                    repository,
                    group,
                    source_id,
                    window,
                    closed=closed_sources[source_id],
                    boundary=boundary,
                )
                requires_audit.update(audit_sources[source_id])
            # Сводка показывает старую неполноту как требующую аудита, но
            # предсказание следующего автоматического сбора её не выбирает.
            pending.update(day for day in missing if day not in requires_audit)

        raw = await raw_for(group, window)

        sources = await _source_outcomes(
            repository,
            group,
            window,
            closed_sources,
            audit_sources,
            boundary,
            latest_runs,
        )

        rows.append(
            GroupCoverage(
                group_id=group.group_id.value,
                title=group.title,
                has_history=group.has_history,
                window_sessions=len(window) if group.has_history else None,
                # Покрытие группы считается ТЕМ ЖЕ правилом, что и исход
                # каждого её источника: сессия закрыта подтверждённым прогоном.
                # Прежде строка группы шла от
                # наблюдений, а раскрытие — от исходов прогонов, и на экране
                # рядом стояли два числа об одном и том же: «11 из 82» сверху и
                # «13 из 82» внутри (FR-032).
                sessions_covered=(len(window) - len(missing)) if group.has_history else None,
                requires_audit=(
                    len(requires_audit)
                    if group.has_history
                    else sum(
                        value
                        for source in sources
                        if isinstance(value := source["requires_audit"], int)
                    )
                ),
                period_from=raw.period_from,
                period_till=raw.period_till,
                gaps=len(missing) if group.has_history else None,
                rows_total=raw.rows_total,
                rows_with_values=raw.rows_with_values,
                sources=sources,
                model_input=group.model_input,
            )
        )

    # Окно догона — не окно группы: у групп они разные (314 сессий у котировок,
    # 82 у позиций), а планирование прогона считает своё. Форме запуска нужно
    # именно оно, и взять его она должна из того же источника, каким
    # пользуется планирование, а не выводить из строк сводки (FR-013b).
    catchup_window = await calendar.window(asof_date, settings.catchup_window_sessions)

    # Сессия, которую возьмёт следующий сбор. Именно она, а не последняя
    # сессия календаря: при отставании сбор берёт несобранную, и обещать
    # сегодняшнюю дату значило бы говорить неправду ровно тогда, когда человек
    # и смотрит на эту строку (FR-024a).
    #
    # Спрашивается у САМОГО СБОРА, а не выводится из строк сводки. Совпасть
    # порядком мало: сбор пропускает сессию, ждущую выдержки после неудачи, и
    # сессию, исчерпавшую предел попыток, — а строка называла ближайшую
    # недостающую независимо от того, возьмут её или нет. Одно правило на
    # обещание и на работу, иначе они разойдутся (FR-054).
    #
    # Когда несобранного нет, следующей будет текущая сессия после порога —
    # ближайший известный торговый день. Будущих дат календарь не знает: он
    # строится по СОСТОЯВШИМСЯ торгам.
    # Дата повтора и расписание новых сессий отдаются раздельно: исчерпание
    # попыток за прошлый день не отменяет новый вечерний сбор (FR-024a).
    from financial_ai.market_data.advance import selectable, session_is_closed, within_attempt_limit

    missing = sorted(pending)
    # Два разных ожидания, и путать их нельзя. Сессию, ждущую выдержки после
    # неудачи, сбор возьмёт САМ — просто позже, — и её дата называется.
    # Исчерпавшую предел попыток не возьмёт никто, пока человек не вмешается
    # (FR-054).
    ready = await selectable(repository, missing, settings)
    retriable = await within_attempt_limit(repository, missing, settings)

    def _first_taken(days: list[dt.date]) -> dt.date:
        """Какую сессию сбор возьмёт первой.

        Тем же правилом, каким берёт сам: свежая сессия идёт отдельным
        прогоном и первой, история — по возрастанию (FR-045). Строка,
        оставшаяся на «самой поздней недостающей», называла середину истории.
        """
        freshest = max(days)
        return freshest if freshest == asof_date else min(days)

    next_blocked = False
    if ready:
        next_session = _first_taken(ready)
    elif retriable:
        next_session = _first_taken(retriable)
    elif missing:
        next_session = None
        next_blocked = True
    else:
        # Недостающего нет: возьмут СЛЕДУЮЩУЮ сессию, а её календарь не знает —
        # он строится по состоявшимся торгам. Называть последнюю собранную
        # значило бы обещать собрать уже собранное (FR-054).
        next_session = None

    now = moscow_now()
    # Расписание новых сессий не зависит от повторов старых. Календарь хранит
    # состоявшиеся торги; следующую дату до обновления называем ожидаемой.
    expected_session = max(now.date(), asof_date + dt.timedelta(days=1))
    while expected_session.weekday() >= 5:
        expected_session += dt.timedelta(days=1)
    awaiting = (
        expected_session == now.date()
        and session_is_closed(expected_session, settings, now)
        and not await repository.is_trading_session(expected_session)
    )

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
        # Оценка по будням, пока биржевой календарь ещё не подтвердил дату.
        "next_expected_session": expected_session.isoformat(),
        # Порог сегодняшней сессии прошёл, а календарь её ещё не подтвердил:
        # идёт ожидание публикации, и «после {порог}» обещало бы наступившее
        # время (FR-054a). Сравнивает сервер: время порога живёт в его настройке.
        "next_expected_awaiting": awaiting,
        "calendar_retry_minutes": settings.market_data_retry_after_minutes,
        # Старые сессии требуют ручного повтора; новые идут по расписанию.
        "next_session_blocked": next_blocked,
        # Названная сессия уже закрыта: ждать её закрытия нечего, сбор возьмёт
        # её ближайшим прогоном. При отставании это обычное дело (FR-054).
        "next_session_closed": bool(
            next_session and session_is_closed(next_session, settings, now)
        ),
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
