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
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import groups
from financial_ai.market_data.calendar import TradingCalendar
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


async def build_report(
    session: AsyncSession, settings: Settings, asof_date: dt.date
) -> dict[str, object]:
    """Собрать сводку по всем группам на дату решения."""
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    rows: list[GroupCoverage] = []
    for group in groups.GROUPS:
        window_size = group.window_sessions(settings)
        window = await calendar.window(asof_date, window_size) if window_size else []

        raw = await repository.group_coverage(
            group.model,
            group.session_column,
            group.value_columns,
            window or None,
        )

        rows.append(
            GroupCoverage(
                group_id=group.group_id.value,
                title=group.title,
                has_history=group.has_history,
                window_sessions=len(window) if group.has_history else None,
                sessions_covered=raw.sessions_covered,
                period_from=raw.period_from,
                period_till=raw.period_till,
                gaps=(len(window) - (raw.sessions_covered or 0)) if group.has_history else None,
                rows_total=raw.rows_total,
                rows_with_values=raw.rows_with_values,
            )
        )

    # Окно догона — не окно группы: у групп они разные (314 сессий у котировок,
    # 82 у позиций), а планирование прогона считает своё. Форме запуска нужно
    # именно оно, и взять его она должна из того же источника, каким
    # пользуется планирование, а не выводить из строк сводки (FR-013b).
    catchup_window = await calendar.window(asof_date, settings.catchup_window_sessions)

    return {
        "asof_date": asof_date.isoformat(),
        "catchup_window": {
            "date_from": catchup_window[0].isoformat() if catchup_window else None,
            "date_till": catchup_window[-1].isoformat() if catchup_window else None,
            "sessions": len(catchup_window),
        },
        "groups": [row.to_dict() for row in rows],
    }
