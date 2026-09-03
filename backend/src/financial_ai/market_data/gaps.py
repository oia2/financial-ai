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
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources import equity_d1


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

    # Хранилище пусто: это не дыра, а отсутствие истории. Догон здесь
    # продублировал бы первичную загрузку дороже — она берёт историю по бумаге
    # целиком, а не перебором дат на всю глубину окна.
    needs_backfill: bool = False

    @property
    def has_gaps(self) -> bool:
        return bool(self.missing_sessions)

    def incomplete_by_session(self) -> dict[dt.date, list[str]]:
        """Полнота окна по сессиям и источникам.

        Гранулярность не случайна: сессия может иметь котировки и не иметь
        позиций. Объявление «сессия неполна» без источника заставило бы считать
        неполным весь срез, тогда как задета одна модальность.
        """
        out: dict[dt.date, set[str]] = {}
        for day in self.missing_sessions:
            out.setdefault(day, set()).add(equity_d1.SOURCE_ID)
        for item in self.unfinished:
            out.setdefault(item.session_date, set()).add(item.source_id)
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

    with_bars = await repository.sessions_with_daily_bars(window)
    collected = await repository.sessions_with_successful_run(window, equity_d1.SOURCE_ID)

    missing = [day for day in window if day not in with_bars and day not in collected]

    unfinished = [
        UnfinishedSource(
            session_date=run.session_date,
            source_id=run.source_id,
            reason=run.failure_reason,
        )
        for run in await repository.failed_runs_for_sessions(window)
        if run.session_date is not None
    ]

    return GapReport(
        asof_date=asof_date,
        window=window,
        missing_sessions=missing,
        unfinished=unfinished,
    )
