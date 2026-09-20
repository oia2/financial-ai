"""Что пропущено: разность календаря и собранного.

Ежедневный цикл собирает одну сессию и о пропусках не знает. День, в который
система не работала, не порождает записи вообще — дыру нельзя обнаружить иначе
как случайно, а признаки модели считаются по окнам до 252 сессий, поэтому один
пропущенный день портит каждое окно, которое его накрывает.

Три решения, на которых держится этот модуль:

- **пропуск вычисляется, а не хранится.** Отметка «последняя собранная дата»
  может разойтись с тем, что реально сохранено; разность разойтись не может.
  Тот же приём принят в `backfill`: отметка о загруженном хранится в самих
  данных;
- **пропуск — это отсутствие котировок И отсутствие успешного прогона.**
  Успешный прогон при нуле наблюдений законен: биржа ответила, данных нет.
  Такая сессия собрана;
- **якорь — котировки.** На них держится пространство строк
  ``price_series_id × дата``. Отсутствие задержанной модальности сессию
  пропущенной не делает, иначе позиции по фьючерсам, недоступные за старые даты
  по своей природе, держали бы окно незакрытым вечно.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import completeness, groups
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.repository import MarketDataRepository


@dataclass(frozen=True, slots=True)
class UnfinishedSource:
    """Источник, оставшийся незакрытым за конкретную сессию."""

    session_date: dt.date
    source_id: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class GapReport:
    """Ответ на вопрос «чего не хватает» в пределах окна."""

    asof_date: dt.date
    window: list[dt.date]
    missing_sessions: list[dt.date]
    unfinished: list[UnfinishedSource] = field(default_factory=list)
    incomplete_sources: dict[dt.date, list[str]] = field(default_factory=dict)

    # Хранилище пусто: это не дыра, а отсутствие истории. Догон здесь
    # продублировал бы первичную загрузку дороже — она берёт историю по бумаге
    # целиком, а не перебором дат на всю глубину окна.
    needs_backfill: bool = False

    @property
    def has_gaps(self) -> bool:
        return bool(self.missing_sessions or self.incomplete_sources)

    def incomplete_by_session(self) -> dict[dt.date, list[str]]:
        """Полнота окна по сессиям и источникам.

        Гранулярность не случайна: сессия может иметь котировки и не иметь
        позиций. Объявление «сессия неполна» без источника заставило бы считать
        неполным весь срез, тогда как задета одна модальность.
        """
        out: dict[dt.date, set[str]] = {
            day: set(sources) for day, sources in self.incomplete_sources.items()
        }
        for item in self.unfinished:
            out.setdefault(item.session_date, set()).add(item.source_id)
        # Совместимость с отчётами, собранными только по котировкам.
        for day in self.missing_sessions:
            if day not in out:
                out.setdefault(day, set()).add("equity_d1")
        return {day: sorted(sources) for day, sources in sorted(out.items())}


async def find_gaps(session: AsyncSession, settings: Settings, asof_date: dt.date) -> GapReport:
    """Найти пропущенные сессии окна, оканчивающегося датой решения."""
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    window = await calendar.window(asof_date, settings.catchup_window_sessions)

    if not await repository.has_any_daily_bars():
        return GapReport(
            asof_date=asof_date,
            window=window,
            missing_sessions=[],
            needs_backfill=True,
        )

    # Набор использует собственные окна групп и то же правило, что сводка и
    # готовность. Сохраняем причины по источнику, включая старые сессии,
    # ожидающие явного аудита; обязательность групп применит финальный gate ML.
    missing: set[dt.date] = set()
    incomplete_sources: dict[dt.date, set[str]] = {}
    unfinished: dict[tuple[dt.date, str], UnfinishedSource] = {}
    for group in groups.GROUPS:
        depth = group.window_sessions(settings)
        if depth is None:
            continue
        source_window = await calendar.window(asof_date, depth)
        closed_by_source = await completeness.closed_by_source(repository, group, source_window)
        if group.group_id is groups.GroupId.QUOTES:
            missing.update(day for day in source_window if day not in closed_by_source["equity_d1"])

        latest = await repository.latest_run_by_session(source_window)
        for source_id in group.source_ids:
            not_closed = set(source_window) - closed_by_source[source_id]
            for day in not_closed:
                incomplete_sources.setdefault(day, set()).add(source_id)
                run = latest.get((day, source_id))
                if run is not None and run.status in {"failed", "stopped", "running"}:
                    unfinished[(day, source_id)] = UnfinishedSource(
                        session_date=day,
                        source_id=source_id,
                        reason=run.failure_reason,
                    )

    return GapReport(
        asof_date=asof_date,
        window=window,
        missing_sessions=sorted(missing),
        unfinished=sorted(unfinished.values(), key=lambda row: (row.session_date, row.source_id)),
        incomplete_sources={
            day: sorted(source_ids) for day, source_ids in sorted(incomplete_sources.items())
        },
    )
