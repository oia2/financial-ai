"""Акции и фонды на доске TQBR (spec 008, FR-060).

С 22.06.2026 биржа торгует паями фондов на той же доске, что и акции: TBEU шёл
на TQTF по 19.06 и на TQBR с 22.06, бумаг на доске за день стало 489 вместо 262.
Сбор берёт доску целиком, и 243 фонда уходили во вход модели как акции.

Испытания держат пять правил: вид бумаги берётся у биржи, группы фондов
показываются отдельно с окном от 22.06, вход модели — только акции, бумага без
вида держит дату, в которую торговалась, а снятая с торгов бумага без вида
выходит из набора с записью в манифест (FR-060d, FR-060f).
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import readiness
from financial_ai.market_data import completeness, coverage, ingest
from financial_ai.market_data.iss.client import ResponseContractError
from financial_ai.market_data.models import MarketAsset
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import securities
from financial_ai.market_data.verification import required_work_keys
from financial_ai.ranking.dataset import DatasetError, build_dataset

pytestmark = pytest.mark.db

# Две сессии до переезда фондов и две после.
SESSIONS = [
    dt.date(2026, 6, 18),
    dt.date(2026, 6, 19),
    dt.date(2026, 6, 22),
    dt.date(2026, 6, 23),
]
ASOF = SESSIONS[-1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        market_data_price_window_sessions=len(SESSIONS),
        market_data_global_window_sessions=len(SESSIONS),
        market_data_positions_window_sessions=2,
        market_data_dataset_root=str(tmp_path),
    )


def _bar(ticker: str, day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id=f"EQ_AST_{ticker}",
        price_series_id=f"EQ_PRS_{ticker}",
        session_date=day,
        open=Decimal("1.5"),
        high=Decimal("1.6"),
        low=Decimal("1.4"),
        close=Decimal("1.55"),
        volume=Decimal("100"),
    )


async def _seed(session: AsyncSession, kinds: dict[str, str] | None = None) -> MarketDataRepository:
    """SBER — все четыре сессии, фонд TBEU — с 22.06, как на бирже."""
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    for ticker in ("SBER", "TBEU"):
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSIONS[0])
        await repository.upsert_price_series(f"EQ_PRS_{ticker}", f"EQ_AST_{ticker}", SESSIONS[0])
    await repository.upsert_daily_bars(
        [_bar("SBER", day) for day in SESSIONS] + [_bar("TBEU", day) for day in SESSIONS[2:]]
    )
    for day in SESSIONS:
        for work_key in required_work_keys("equity_d1"):
            await repository.record_work_evidence(
                source_id="equity_d1",
                session_date=day,
                work_key=work_key,
                result_kind="value",
                reason_code="test_verified_work",
                origin_run_id=None,
            )
    await repository.update_security_kinds(
        {"EQ_AST_SBER": "share", "EQ_AST_TBEU": "fund"} if kinds is None else kinds
    )
    await session.commit()
    return repository


def _group(report: dict[str, object], name: str) -> dict[str, object]:
    rows = report["groups"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["group"] == name)


# --- сводка (FR-060a, FR-060b, FR-060c, FR-060e) -----------------------------


async def test_funds_are_a_separate_group_from_june_22(
    db_session: AsyncSession, settings: Settings
) -> None:
    await _seed(db_session)

    report = await coverage.build_report(db_session, settings, ASOF)

    shares = _group(report, "quotes")
    assert shares["title"] == "котировки акций"
    assert shares["model_input"] is True
    assert shares["window_sessions"] == 4
    assert shares["rows_total"] == 4

    # Окно фондов — с 22.06: раньше они торговались на другой доске, и «4 из 4»
    # было бы неправдой (FR-060b).
    funds = _group(report, "fund_quotes")
    assert funds["title"] == "котировки фондов"
    assert funds["model_input"] is False
    assert funds["window_sessions"] == 2
    assert funds["sessions_covered"] == 2
    assert funds["rows_total"] == 2
    assert funds["period_from"] == "2026-06-22"

    # Состав — по акциям: у фондов фьючерсов не бывает (FR-060e).
    universe = report["universe"]
    assert isinstance(universe, dict)
    assert universe["assets"] == 1


async def test_source_window_is_the_union_of_its_groups(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Окно фондов короче, но сбор котировок им не урезается (FR-060b).

    Присваивание окна последней группой источника урезало бы сбор котировок
    акций до 22.06.2026.
    """
    repository = await _seed(db_session)

    windows = await completeness.source_windows(repository, settings, ASOF)

    assert windows["equity_d1"] == frozenset(SESSIONS)


# --- вход модели (FR-060c, FR-060d) ------------------------------------------


async def test_dataset_carries_shares_only(db_session: AsyncSession, settings: Settings) -> None:
    await _seed(db_session)

    dataset = await build_dataset(db_session, settings, ASOF)

    assert [asset.asset_id for asset in dataset.assets] == ["EQ_AST_SBER"]


async def test_dataset_refuses_a_candidate_of_unknown_kind(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Бумага без вида, торговавшаяся в дату решения, не выпадает молча и не
    проходит как акция (FR-060d)."""
    await _seed(db_session, kinds={"EQ_AST_SBER": "share"})

    with pytest.raises(DatasetError, match="вид бумаги не получен"):
        await build_dataset(db_session, settings, ASOF)


async def _delisted(repository: MarketDataRepository, ticker: str = "OLDX") -> None:
    """Бумага без вида, снятая с торгов до даты решения."""
    await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSIONS[0])
    await repository.upsert_price_series(f"EQ_PRS_{ticker}", f"EQ_AST_{ticker}", SESSIONS[0])
    await repository.upsert_daily_bars([_bar(ticker, day) for day in SESSIONS[:2]])


async def test_delisted_asset_of_unknown_kind_leaves_the_dataset_on_record(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Снятая бумага без вида выходит из набора, исключение — в манифесте и
    дайджесте; без исключений дайджест прежний (FR-060f)."""
    repository = await _seed(db_session)
    before = await build_dataset(db_session, settings, ASOF)
    assert before.excluded == []

    await _delisted(repository)
    await db_session.commit()

    dataset = await build_dataset(db_session, settings, ASOF)

    assert [asset.asset_id for asset in dataset.assets] == ["EQ_AST_SBER"]
    assert dataset.excluded == [{"asset_id": "EQ_AST_OLDX", "reason": "kind_unknown"}]
    assert dataset.digest != before.digest
    manifest = json.loads((dataset.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["excluded_assets"] == [{"asset_id": "EQ_AST_OLDX", "reason": "kind_unknown"}]
    earlier = json.loads((before.path / "manifest.json").read_text(encoding="utf-8"))
    assert earlier["excluded_assets"] == []


async def test_asset_without_kind_holds_only_the_dates_it_traded(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Дату держит бумага без вида, торговавшаяся в неё, — и только её (FR-060d).

    Прежде проверялась вся история: снятая годы назад бумага останавливала
    модель навсегда, а новая — делала неготовыми и прошлые даты.
    """
    repository = await _seed(db_session, kinds={"EQ_AST_SBER": "share"})

    blocked = await readiness._kind_unknown_on(repository, settings, SESSIONS)

    # TBEU без вида торгуется с 22.06: 18.06 и 19.06 он не держит.
    assert blocked == {dt.date(2026, 6, 22), dt.date(2026, 6, 23)}

    await repository.update_security_kinds({"EQ_AST_TBEU": "fund"})
    await _delisted(repository)
    await db_session.commit()

    # Снятая бумага без вида даты решения не держит.
    assert await readiness._kind_unknown_on(repository, settings, [ASOF]) == set()


async def test_reference_is_asked_again_for_a_candidate_without_kind(
    db_session: AsyncSession,
) -> None:
    """Бумага без вида в последней сессии — справочник спрашивается, не дожидаясь
    суток; снятая с торгов — нет, иначе он перезапрашивался бы без конца."""
    repository = await _seed(db_session, kinds={"EQ_AST_SBER": "share"})
    now = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="lots-today",
        source_id=securities.SOURCE_ID,
        status="ok",
        started_at=now,
        finished_at=now,
        session_date=ASOF,
        rows_written=1,
        coverage_version=2,
        coverage_reason="verified_work_evidence",
    )
    await db_session.commit()

    assert await ingest.reference_is_due(repository, securities.SOURCE_ID, ASOF)

    await repository.update_security_kinds({"EQ_AST_TBEU": "fund"})
    await _delisted(repository)
    await db_session.commit()

    assert not await ingest.reference_is_due(repository, securities.SOURCE_ID, ASOF)


# --- вид бумаги от биржи (FR-060) ---------------------------------------------


class ListingIss:
    """Список доски и описания бумаг, как их отдаёт биржа."""

    def __init__(self, types: dict[str, str], groups_by_ticker: dict[str, str | None]) -> None:
        self.types = types
        self.groups_by_ticker = groups_by_ticker
        self.described: list[str] = []

    async def fetch_equity_lot_sizes(self) -> dict[str, int]:
        return dict.fromkeys(self.types, 1)

    async def fetch_equity_isins(self) -> dict[str, str]:
        return {}

    async def fetch_equity_security_types(self) -> dict[str, str]:
        return dict(self.types)

    async def fetch_security_group(self, secid: str) -> str | None:
        self.described.append(secid)
        return self.groups_by_ticker.get(secid)


async def _assets(repository: MarketDataRepository, tickers: list[str]) -> None:
    for ticker in tickers:
        await repository.upsert_asset(f"EQ_AST_{ticker}", ticker, SESSIONS[0])
        await repository.upsert_price_series(f"EQ_PRS_{ticker}", f"EQ_AST_{ticker}", SESSIONS[0])
    await repository.upsert_daily_bars([_bar(ticker, ASOF) for ticker in tickers])


async def test_kind_comes_from_the_board_listing_and_the_description(
    db_session: AsyncSession,
) -> None:
    """Код доски для торгуемых, описание — для снятой с торгов бумаги."""
    repository = MarketDataRepository(db_session)
    await _assets(repository, ["SBER", "SBERP", "OKEY", "TBEU", "LQDT", "RU000A0ZZ422"])
    iss = ListingIss(
        types={"SBER": "1", "SBERP": "2", "OKEY": "D", "TBEU": "J", "LQDT": "J"},
        groups_by_ticker={"RU000A0ZZ422": "stock_ppif"},
    )

    await securities.sync_lot_sizes(iss, repository)  # type: ignore[arg-type]
    await db_session.commit()

    assert await repository.share_asset_ids() == {"EQ_AST_SBER", "EQ_AST_SBERP", "EQ_AST_OKEY"}
    assert await repository.fund_asset_ids() == {
        "EQ_AST_TBEU",
        "EQ_AST_LQDT",
        "EQ_AST_RU000A0ZZ422",
    }
    # Описание спрашивается только у бумаги, которой нет в списке доски.
    assert iss.described == ["RU000A0ZZ422"]


async def test_unrecognised_kind_of_a_candidate_is_a_source_failure(
    db_session: AsyncSession,
) -> None:
    """Нераспознанный вид торгуемой бумаги — отказ с её названием, а не догадка."""
    repository = MarketDataRepository(db_session)
    await _assets(repository, ["SBER", "ODDX"])
    iss = ListingIss(types={"SBER": "1", "ODDX": "Z"}, groups_by_ticker={"ODDX": "stock_bonds"})

    with pytest.raises(ResponseContractError, match="ODDX"):
        await securities.sync_lot_sizes(iss, repository)  # type: ignore[arg-type]

    # Распознанное записано, нераспознанное осталось без вида.
    assert await repository.share_asset_ids() == {"EQ_AST_SBER"}
    assert await repository.assets_without_kind() == {"EQ_AST_ODDX": "ODDX"}


class BrokenDescriptionIss(ListingIss):
    """Описание одной бумаги нарушает контракт ответа."""

    async def fetch_security_group(self, secid: str) -> str | None:
        if secid == "BRKN":
            self.described.append(secid)
            raise ResponseContractError("ответ MOEX ISS не содержит блок 'description'")
        return await super().fetch_security_group(secid)


async def test_unrecognised_delisted_kind_does_not_fail_the_reference(
    db_session: AsyncSession,
) -> None:
    """Снятая бумага с нераспознанным видом или сломанным описанием справочник
    не роняет и разбор остальных не обрывает (FR-060, FR-060f)."""
    repository = MarketDataRepository(db_session)
    await _assets(repository, ["SBER"])
    for ticker in ("BRKN", "ODDX", "OLDF"):
        await _delisted(repository, ticker)
    iss = BrokenDescriptionIss(
        types={"SBER": "1"},
        groups_by_ticker={"ODDX": "stock_bonds", "OLDF": "stock_ppif"},
    )

    await securities.sync_lot_sizes(iss, repository)  # type: ignore[arg-type]
    await db_session.commit()

    assert sorted(iss.described) == ["BRKN", "ODDX", "OLDF"]
    assert await repository.fund_asset_ids() == {"EQ_AST_OLDF"}
    kinds = {
        asset.asset_id: asset.security_kind
        for asset in (await db_session.scalars(select(MarketAsset))).all()
    }
    assert kinds["EQ_AST_BRKN"] is None
    assert kinds["EQ_AST_ODDX"] is None
