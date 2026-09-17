"""Источник состава инструментов на записанных ответах биржи.

Испытания изменения состава (spec 008, FR-029) идут по записанным ответам, а не
по придуманным: все дефекты связи в этом проекте были расхождением с настоящим
источником, а тесты на подделках при этом проходили.

Ответы лежат в `tests/fixtures/instruments/` и сняты 2026-09-17. Сценарий
меняет их поштучно — так и выглядит изменение состава: вчерашний ответ плюс
одна разница.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_ai.market_data.repository import DailyBar, MarketDataRepository

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "instruments"


def _block(name: str, key: str) -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    block = payload[key]
    columns = block["columns"]
    return [dict(zip(columns, row, strict=False)) for row in block["data"]]


def recorded_series() -> list[dict[str, Any]]:
    """Серии срочного рынка, как их отдала биржа."""
    return _block("futures_series", "series")


def recorded_open_interest() -> dict[str, int]:
    """Открытый интерес по семействам контрактов."""
    totals: dict[str, int] = {}
    for row in _block("futures_securities", "securities"):
        code = str(row.get("ASSETCODE") or "").strip().upper()
        if not code:
            continue
        totals[code] = totals.get(code, 0) + int(row.get("PREVOPENPOSITION") or 0)
    return totals


def recorded_isins() -> dict[str, str]:
    """Устойчивые идентификаторы бумаг доски."""
    return {
        str(row["SECID"]).strip().upper(): str(row["ISIN"]).strip().upper()
        for row in _block("equity_securities", "securities")
        if row.get("SECID") and row.get("ISIN")
    }


def recorded_emitters() -> dict[str, str]:
    """Идентификаторы эмитентов из описаний инструментов.

    В фикстурах записаны три описания; для остальных инструментов эмитент
    выводится из того же правила, по которому биржа его и присваивает: одна
    бумага — один эмитент. Выдумывать расхождение нельзя, поэтому сценарий,
    которому нужен чужой эмитент, задаёт его явно.
    """
    emitters: dict[str, str] = {}
    for path in FIXTURES.glob("description_*.json"):
        rows = _block(path.stem, "description")
        secid = next((r["value"] for r in rows if r["name"] == "SECID"), None)
        emitter = next((r["value"] for r in rows if r["name"] == "EMITTER_ID"), None)
        if secid and emitter:
            emitters[str(secid).strip().upper()] = str(emitter).strip()
    return emitters


class RecordedIss:
    """Биржа, отвечающая записанными ответами.

    Считает обращения: цена сверки эмитента должна быть пропорциональна
    изменению состава, а не размеру доски, и это проверяется числом.
    """

    def __init__(
        self,
        *,
        series: list[dict[str, Any]] | None = None,
        open_interest: dict[str, int] | None = None,
        isins: dict[str, str] | None = None,
        emitters: dict[str, str] | None = None,
    ) -> None:
        self.series = recorded_series() if series is None else series
        self.open_interest = recorded_open_interest() if open_interest is None else open_interest
        self.isins = recorded_isins() if isins is None else isins
        self.emitters = recorded_emitters() if emitters is None else emitters
        self.description_calls: list[str] = []

    async def fetch_futures_series(self) -> list[dict[str, Any]]:
        return list(self.series)

    async def fetch_futures_open_interest(self) -> dict[str, int]:
        return dict(self.open_interest)

    async def fetch_equity_isins(self) -> dict[str, str]:
        return dict(self.isins)

    async def fetch_emitter_id(self, secid: str) -> str | None:
        code = secid.strip().upper()
        self.description_calls.append(code)
        return self.emitters.get(code)


def without(series: list[dict[str, Any]], **match: str) -> list[dict[str, Any]]:
    """Ответ без строк, подходящих под условие: инструмент исчез."""
    return [row for row in series if any(row.get(k) != v for k, v in match.items())]


async def seed_assets(
    repository: MarketDataRepository, session_date: dt.date, tickers: list[str]
) -> None:
    """Бумаги, торговавшиеся в эту сессию.

    Знаменатель состава — именно они: связь ставится тем бумагам, что на доске
    есть, а не всем, у кого биржа знает контракт.
    """
    await repository.add_trading_sessions([session_date])
    bars = []
    for ticker in tickers:
        asset_id = f"EQ_AST_{ticker}"
        series_id = f"EQ_PRS_{ticker}"
        await repository.upsert_asset(asset_id, ticker, session_date)
        await repository.upsert_price_series(series_id, asset_id, session_date)
        bars.append(
            DailyBar(
                asset_id=asset_id,
                price_series_id=series_id,
                session_date=session_date,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        )
    await repository.upsert_daily_bars(bars)
