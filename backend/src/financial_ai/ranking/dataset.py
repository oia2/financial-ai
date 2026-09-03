"""Материализация неизменяемого набора входных данных.

Набор — срез рыночных рядов за окно решения плюс манифест с дайджестом. После
материализации он **не меняется никогда**: доезд запоздавших данных порождает
новый набор с новым дайджестом, а не правит старый.

Ради чего это:

- **воспроизводимость** — дайджест опознаёт вход, и через месяц можно точно
  сказать, на каких данных получено ранжирование. Для системы, распоряжающейся
  деньгами, это не формальность;
- **экономия** — из окна в 314 сессий новой является одна. Пересылать
  остальные 313 при каждом запросе значило бы гонять 99,7% уже известного;
- **развязка** — звено ранжирования не знает схемы хранилища и не имеет
  реквизитов доступа к нему.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data import gaps
from financial_ai.market_data.calendar import TradingCalendar
from financial_ai.market_data.models import (
    EquityAggregate,
    EquityDailyBar,
    FuturesPosition,
    GlobalDailySeries,
)
from financial_ai.market_data.repository import MarketDataRepository

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
PRICES_NAME = "prices.json"
GLOBAL_NAME = "global.json"
POSITIONS_NAME = "positions.json"
AGGREGATES_NAME = "aggregates.json"
SECTORS_NAME = "sectors.json"

POSITION_FIELDS = ["fiz_long", "fiz_short", "jur_long", "jur_short"]
AGGREGATE_FIELDS = ["value", "num_trades", "waprice"]


class DatasetError(RuntimeError):
    """Набор входных данных не удалось собрать."""


# Ключ ряда: экономический актив плюс его ценовой ряд. Оба нужны — у актива
# может быть несколько несшиваемых рядов.
SeriesKey = tuple[str, str]

# Строка объявления полноты окна: дата сессии и незакрытые за неё источники.
IncompleteRow = dict[str, str | list[str]]
SeriesRows = dict[SeriesKey, list[list[str | None]]]

PRICE_FIELDS = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Актив, данные которого вошли в набор."""

    asset_id: str
    price_series_id: str


@dataclass(frozen=True, slots=True)
class Dataset:
    """Материализованный набор."""

    ref: str
    digest: str
    asof_date: dt.date
    sessions: list[dt.date]
    assets: list[AssetRef]
    windows: dict[str, int]
    path: Path

    # Полнота окна: какие сессии и по каким источникам остались несобранными.
    # Поле присутствует ВСЕГДА — пустой перечень это значимое утверждение
    # «окно полно», и от отсутствия высказывания оно отличается.
    incomplete: list[IncompleteRow]


async def build_dataset(session: AsyncSession, settings: Settings, asof_date: dt.date) -> Dataset:
    """Собрать набор входных данных на дату решения.

    Окно отсчитывается в ТОРГОВЫХ сессиях: на новогодних каникулах отсчёт по
    календарным дням ошибся бы почти на две недели.
    """
    repository = MarketDataRepository(session)
    calendar = TradingCalendar(repository)

    if not await calendar.is_session(asof_date):
        raise DatasetError(
            f"{asof_date} не является торговой сессией: набор на эту дату не имеет смысла"
        )

    price_sessions = await calendar.window(asof_date, settings.market_data_price_window_sessions)
    global_sessions = await calendar.window(asof_date, settings.market_data_global_window_sessions)

    bars = await repository.daily_bars_for_window(price_sessions)
    if not bars:
        raise DatasetError(f"за окно, оканчивающееся {asof_date}, нет ни одного наблюдения")

    global_rows = await repository.global_values_for_window(global_sessions)

    # У позиций своё окно: они нужны модели на 82 сессии, а не на 314.
    position_sessions = await calendar.window(
        asof_date, settings.market_data_positions_window_sessions
    )
    position_rows = await repository.positions_for_window(position_sessions)

    # Агрегаты идут по окну цен: модель заводит их тем же слоем состояния,
    # что и котировки (`data_plane_step4`), отдельного окна у них нет.
    aggregate_rows = await repository.aggregates_for_window(price_sessions)

    # Секторы — справочник без оси сессий: у признаков отраслевой относительной
    # силы и широты нет истории принадлежности, они читают текущее значение.
    sector_map = await repository.sectors()

    prices = _serialize_prices(bars, price_sessions)
    aggregates = _serialize_aggregates(aggregate_rows, price_sessions)
    globals_payload = _serialize_globals(global_rows, global_sessions)
    positions_payload = _serialize_positions(position_rows, position_sessions)
    assets = [AssetRef(asset_id=a, price_series_id=s) for a, s in sorted(prices)]

    windows = {
        "price_sessions": len(price_sessions),
        "aggregate_sessions": len(price_sessions),
        "global_sessions": len(global_sessions),
        "positions_sessions": settings.market_data_positions_window_sessions,
    }

    # Полнота окна — утверждение отправителя: из рядов её не вывести, пропуск
    # там выглядит одинаково и когда бумага не торговалась, и когда за день не
    # ходили на биржу.
    report = await gaps.find_gaps(session, settings, asof_date)
    incomplete: list[IncompleteRow] = [
        {"session_date": day.isoformat(), "sources": sources}
        for day, sources in report.incomplete_by_session().items()
        if day in set(price_sessions)
    ]

    digest = _digest(
        asof_date,
        price_sessions,
        prices,
        globals_payload,
        positions_payload,
        aggregates,
        sector_map,
        incomplete,
    )
    root = Path(settings.market_data_dataset_root)
    path = root / f"{asof_date.isoformat()}-{digest[:16]}"

    if path.exists():
        # Тот же дайджест — тот же набор. Пересобирать нечего: он неизменяем.
        logger.info("набор %s уже материализован", path.name)
    else:
        _write(
            path,
            asof_date,
            price_sessions,
            position_sessions,
            prices,
            globals_payload,
            positions_payload,
            aggregates,
            sector_map,
            incomplete,
            assets,
            windows,
            digest,
        )

    return Dataset(
        ref=path.as_uri(),
        digest=f"sha256:{digest}",
        asof_date=asof_date,
        sessions=price_sessions,
        assets=assets,
        windows=windows,
        path=path,
        incomplete=incomplete,
    )


def _serialize_prices(bars: list[EquityDailyBar], sessions: list[dt.date]) -> SeriesRows:
    """Ряды в компактной раскладке: общая ось сессий, значения позиционно.

    Повторение имён полей в каждой строке удваивает объём без всякой пользы.
    """
    index = {day: position for position, day in enumerate(sessions)}
    series: SeriesRows = {}

    for bar in bars:
        position = index.get(bar.session_date)
        if position is None:
            continue
        row = series.setdefault(
            (bar.asset_id, bar.price_series_id), [[None] * len(PRICE_FIELDS) for _ in sessions]
        )
        row[position] = [
            _as_text(bar.open),
            _as_text(bar.high),
            _as_text(bar.low),
            _as_text(bar.close),
            _as_text(bar.volume),
        ]

    return series


def _serialize_globals(
    rows: list[GlobalDailySeries], sessions: list[dt.date]
) -> dict[str, list[str | None]]:
    index = {day: position for position, day in enumerate(sessions)}
    out: dict[str, list[str | None]] = {}
    for row in rows:
        values = out.setdefault(row.series_id, [None] * len(sessions))
        position = index.get(row.session_date)
        if position is not None:
            values[position] = _as_text(row.value)
    return out


def _serialize_positions(
    rows: list[FuturesPosition], sessions: list[dt.date]
) -> dict[str, list[list[str | None]]]:
    """Позиции в той же компактной раскладке, что и цены.

    Пропуск остаётся пропуском: отсутствие позиций и нулевые позиции — разные
    факты, и ветка позиций не должна кодировать артефакт покрытия как сигнал.
    """
    index = {day: position for position, day in enumerate(sessions)}
    out: dict[str, list[list[str | None]]] = {}
    for row in rows:
        position = index.get(row.session_date)
        if position is None:
            continue
        series = out.setdefault(row.asset_id, [[None] * len(POSITION_FIELDS) for _ in sessions])
        series[position] = [
            _as_text(row.fiz_long),
            _as_text(row.fiz_short),
            _as_text(row.jur_long),
            _as_text(row.jur_short),
        ]
    return out


def _serialize_aggregates(rows: list[EquityAggregate], sessions: list[dt.date]) -> SeriesRows:
    """Дневные агрегаты торгов в той же раскладке, что и цены.

    Ключ тот же, что у котировок: агрегаты приходят по тому же ценовому ряду и
    сшиваются вместе с ним. ``VALUE``, ``NUMTRADES`` и ``WAPRICE`` модель
    заводит в слой состояния (`data_plane_step4`), поэтому пропуск остаётся
    пропуском и здесь.
    """
    index = {day: position for position, day in enumerate(sessions)}
    series: SeriesRows = {}

    for row in rows:
        position = index.get(row.session_date)
        if position is None:
            continue
        values = series.setdefault(
            (row.asset_id, row.price_series_id),
            [[None] * len(AGGREGATE_FIELDS) for _ in sessions],
        )
        values[position] = [
            _as_text(row.value),
            _as_text(row.num_trades),
            _as_text(row.waprice),
        ]

    return series


def _as_text(value: Decimal | None) -> str | None:
    """Значения передаются строками: точность обязана дойти без искажений.

    ``None`` остаётся ``None`` — пропуск не заменяется нулём.
    """
    return None if value is None else str(value)


def _digest(
    asof_date: dt.date,
    sessions: list[dt.date],
    prices: SeriesRows,
    globals_payload: dict[str, list[str | None]],
    positions_payload: dict[str, list[list[str | None]]],
    aggregates: SeriesRows,
    sector_map: dict[str, str | None],
    incomplete: list[IncompleteRow],
) -> str:
    """Дайджест от содержимого.

    Считается только по данным: момент материализации в него не входит,
    иначе два одинаковых набора получили бы разные дайджесты.
    """
    payload = {
        "asof_date": asof_date.isoformat(),
        "sessions": [d.isoformat() for d in sessions],
        "series": {f"{a}|{s}": rows for (a, s), rows in sorted(prices.items())},
        "global": {k: globals_payload[k] for k in sorted(globals_payload)},
        "positions": {k: positions_payload[k] for k in sorted(positions_payload)},
        "aggregates": {f"{a}|{s}": rows for (a, s), rows in sorted(aggregates.items())},
        "sectors": {k: sector_map[k] for k in sorted(sector_map)},
        # Полнота входит в содержимое намеренно: два набора с одинаковыми
        # рядами и разной полнотой окна — РАЗНЫЕ входы, и одинаковый
        # идентификатор позволил бы скрыть различие.
        "incomplete": incomplete,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write(
    path: Path,
    asof_date: dt.date,
    sessions: list[dt.date],
    position_sessions: list[dt.date],
    prices: SeriesRows,
    globals_payload: dict[str, list[str | None]],
    positions_payload: dict[str, list[list[str | None]]],
    aggregates: SeriesRows,
    sector_map: dict[str, str | None],
    incomplete: list[IncompleteRow],
    assets: list[AssetRef],
    windows: dict[str, int],
    digest: str,
) -> None:
    path.mkdir(parents=True, exist_ok=True)

    (path / PRICES_NAME).write_text(
        json.dumps(
            {
                "sessions": [d.isoformat() for d in sessions],
                "fields": PRICE_FIELDS,
                "series": [
                    {"asset_id": a, "price_series_id": s, "bars": rows}
                    for (a, s), rows in sorted(prices.items())
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    (path / GLOBAL_NAME).write_text(
        json.dumps(
            {"sessions": [d.isoformat() for d in sessions], "series": globals_payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    (path / POSITIONS_NAME).write_text(
        json.dumps(
            {
                "sessions": [d.isoformat() for d in position_sessions],
                "fields": POSITION_FIELDS,
                "series": [
                    {"asset_id": a, "rows": rows} for a, rows in sorted(positions_payload.items())
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    (path / AGGREGATES_NAME).write_text(
        json.dumps(
            {
                "sessions": [d.isoformat() for d in sessions],
                "fields": AGGREGATE_FIELDS,
                "series": [
                    {"asset_id": a, "price_series_id": s, "rows": rows}
                    for (a, s), rows in sorted(aggregates.items())
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    (path / SECTORS_NAME).write_text(
        json.dumps(
            # Оси сессий здесь нет намеренно: справочник отражает текущую
            # принадлежность, а не её историю.
            {"sectors": {k: sector_map[k] for k in sorted(sector_map)}},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    (path / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "asof_date": asof_date.isoformat(),
                "digest": f"sha256:{digest}",
                "windows": windows,
                "incomplete": incomplete,
                "session_count": len(sessions),
                "asset_count": len(assets),
                "assets": [
                    {"asset_id": a.asset_id, "price_series_id": a.price_series_id} for a in assets
                ],
                "materialized_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        "набор на %s материализован: сессий %d, активов %d, дайджест %s",
        asof_date,
        len(sessions),
        len(assets),
        digest[:16],
    )


def prune_datasets(settings: Settings, now: dt.datetime | None = None) -> int:
    """Удалить наборы старше настроенного срока.

    Неизменяемость означает накопление: без правила очистки место закончится.
    """
    root = Path(settings.market_data_dataset_root)
    if not root.exists():
        return 0

    moment = now or dt.datetime.now(dt.UTC)
    cutoff = (moment - dt.timedelta(days=settings.market_data_dataset_retention_days)).date()

    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        asof = _asof_from_name(entry.name)
        if asof is not None and asof < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
            logger.info("набор %s удалён по сроку хранения", entry.name)
    return removed


def _asof_from_name(name: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(name[:10])
    except ValueError:
        return None
