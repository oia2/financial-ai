"""Глобальные дневные ряды с MOEX ISS.

Индексы, курс USD и Brent приходят с одной биржи одним клиентом и различаются
только тремя вещами: раздел торгов, бумага и колонка со значением. Поэтому здесь
один загрузчик и объявление рядов, а не четыре почти одинаковых модуля.

Перенесено из `pipelines/iss_indices_close_value_sync/`,
`pipelines/usd000utstom_history_sync/` и `pipelines/br_continuous_history_sync/`
(`MR-MASTER-DRO`, `f07295e`): оттуда взяты разделы торгов, бумаги и колонки.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from financial_ai.market_data.interrupt import SourcePartialError, SourceStoppedError
from financial_ai.market_data.iss.client import IssClient
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.market_data.sources.equity_d1 import to_decimal
from financial_ai.market_data.sources.trading_calendar import parse_date

logger = logging.getLogger(__name__)

SOURCE_ID = "global_series"


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """Объявление одного глобального ряда."""

    series_id: str
    secid: str
    engine: str
    market: str
    board: str | None = None
    value_column: str = "CLOSE"

    def columns(self) -> tuple[str, ...]:
        return ("SECID", "TRADEDATE", self.value_column)


# Разделы торгов взяты из конфигурации исходных пайплайнов, а не подобраны:
# индексы живут в engine=stock/market=index, валюта — в currency/selt.
ISS_SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("IMOEX", "IMOEX", engine="stock", market="index"),
    SeriesSpec("RTSI", "RTSI", engine="stock", market="index"),
    SeriesSpec("RGBI", "RGBI", engine="stock", market="index"),
    SeriesSpec("RVI", "RVI", engine="stock", market="index"),
    SeriesSpec("USD_ISS", "USD000UTSTOM", engine="currency", market="selt", board="CETS"),
)


async def sync_iss_series(
    client: IssClient,
    repository: MarketDataRepository,
    session_date: dt.date,
    specs: tuple[SeriesSpec, ...] = ISS_SERIES,
) -> int:
    """Собрать значения глобальных рядов за одну торговую сессию."""
    return await sync_iss_series_range(client, repository, session_date, session_date, specs)


async def sync_iss_series_range(
    client: IssClient,
    repository: MarketDataRepository,
    date_from: dt.date,
    date_till: dt.date,
    specs: tuple[SeriesSpec, ...] = ISS_SERIES,
) -> int:
    """Собрать значения глобальных рядов за период.

    Биржа отдаёт историю ряда диапазоном, и для догона это принципиально:
    дыра любой длины закрывается **одним** обращением на ряд вместо одного на
    каждую сессию. Ежедневный добор — частный случай с совпадающими границами.

    Неудача одного ряда не отменяет остальные: ряды независимы, и терять
    собранное из-за недоступности одного индекса незачем. **Но и успехом она
    не становится.** Обход продолжается, полученное сохраняется, а итог
    сообщает неуспех и называет несобранные ряды: пока он сообщал успех, один
    полученный ряд из пяти закрывал сессию всем пяти, и остальные четыре не
    попадали больше ни в один план (FR-032).

    Пустой ответ и сломанный контракт здесь разные исходы. Ряд, за который
    биржа ответила корректно и значений не дала, работу закрывает: спрашивать
    нечего. Ряд, обращение за которым упало, остаётся работой.
    """
    written = 0
    unfinished: list[str] = []
    for spec in specs:
        should_stop = getattr(client, "should_stop", None)
        if should_stop is not None and should_stop():
            raise SourceStoppedError(written)
        try:
            values = await _fetch_series(client, spec, date_from, date_till)
        except SourceStoppedError as error:
            raise SourceStoppedError(written + error.rows_written) from error
        except SeriesFetchError as error:
            # Ряд не получен. Дальше идём — ряды независимы, — но запоминаем:
            # незавершённое обязано дожить до исхода.
            logger.warning("глобальный ряд %s не собран: %s", spec.series_id, error)
            unfinished.append(spec.series_id)
            continue
        if values:
            written += await repository.upsert_global_values(spec.series_id, values)

    if unfinished:
        raise SourcePartialError(
            rows_written=written,
            detail=(
                f"не собраны ряды: {', '.join(unfinished)}"
                f" (получено {len(specs) - len(unfinished)} из {len(specs)})"
            ),
            unfinished=tuple(unfinished),
        )
    return written


class SeriesFetchError(RuntimeError):
    """Обращение за одним рядом не удалось.

    Отдельный тип, а не пустой словарь: пустой словарь неотличим от
    корректного ответа без значений, и разница между «биржа сказала, что
    данных нет» и «мы не смогли спросить» пропадала ровно там, где она нужна
    (FR-032).
    """


async def _fetch_series(
    client: IssClient, spec: SeriesSpec, date_from: dt.date, date_till: dt.date
) -> dict[dt.date, Decimal | None]:
    """Значения одного ряда за период.

    Пустой словарь означает корректный ответ без значений и **только** его.
    Несостоявшееся обращение поднимает ``SeriesFetchError``: раньше оба случая
    возвращали ``{}``, и неизвестность записывалась успехом.
    """
    try:
        rows = await client.fetch_security_history(
            spec.secid,
            date_from.isoformat(),
            date_till.isoformat(),
            spec.columns(),
            engine=spec.engine,
            market=spec.market,
            board=spec.board,
        )
    except SourceStoppedError:
        raise
    except Exception as error:
        raise SeriesFetchError(f"{spec.series_id}: {error}") from error

    return rows_to_values(rows, spec.value_column)


def rows_to_values(
    rows: list[dict[str, object]], value_column: str
) -> dict[dt.date, Decimal | None]:
    """Преобразовать ответ биржи в значения по датам.

    Пропуск остаётся пропуском: отсутствие значения не заменяется нулём.
    """
    values: dict[dt.date, Decimal | None] = {}
    for row in rows:
        day = parse_date(row.get("TRADEDATE"))
        if day is None:
            continue
        values[day] = to_decimal(row.get(value_column))
    return values
