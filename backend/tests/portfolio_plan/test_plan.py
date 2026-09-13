"""Арифметика плана — specs/007-daily-ml-lifecycle/data-model.md §7.

Каждый тест защищает одно правило, перенесённое из ребалансировки исследования.
Все они нарушаются незаметно: план по-прежнему выглядит правдоподобно, но
перестаёт быть тем правилом, которое проверяли.

Стенд: двадцать активов, капитал 100 000 ₽, равные доли по 5 000 ₽. Числа
сходятся на бумаге — см. комментарий в `conftest.py`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.portfolio_plan import plan as plan_module

from .conftest import (
    ALL_ASSETS,
    ASOF,
    position,
    seed_account,
    seed_market,
    seed_ranking,
)

pytestmark = pytest.mark.db

POLICY = "equal_top20"


async def _plan(
    session: AsyncSession,
    settings: Settings,
    policy: str = POLICY,
    **kwargs: Decimal,
) -> dict[str, object]:
    return await plan_module.build(
        session, settings, plan_module.PlanRequest(policy=policy, **kwargs)
    )


def _row(plan: dict[str, object], ticker: str) -> dict[str, object]:
    positions: list[dict[str, object]] = plan["positions"]  # type: ignore[assignment]
    return next(row for row in positions if row["ticker"] == ticker)


def _capital(plan: dict[str, object]) -> dict[str, str]:
    capital: dict[str, str] = plan["capital"]  # type: ignore[assignment]
    return capital


async def test_quantity_is_a_whole_number_of_lots(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Количество кратно лоту: дробное количество на бирже неисполнимо."""
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    positions: list[dict[str, object]] = plan["positions"]  # type: ignore[assignment]
    for row in positions:
        lot = int(str(row["lot_size"]))
        assert Decimal(str(row["target_quantity"])) == Decimal(int(str(row["target_lots"])) * lot)


async def test_rounding_remainder_stays_cash(db_session: AsyncSession, settings: Settings) -> None:
    """Остаток округления остаётся деньгами и другим активам не достаётся.

    SBER: доля 5 000 ₽, лот стоит 3 000 ₽ — один лот, 2 000 ₽ мимо.
    GAZP: лот стоит 1 300 ₽ — три лота на 3 900 ₽, 1 100 ₽ мимо.
    Итого распределено 96 900 ₽ из 100 000 ₽.
    """
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    assert _row(plan, "SBER")["target_lots"] == 1
    assert _row(plan, "SBER")["target_quantity"] == "10"
    assert _row(plan, "GAZP")["target_lots"] == 3
    assert _row(plan, "LKOH")["target_lots"] == 2

    assert Decimal(_capital(plan)["allocated"]) == Decimal("96900.00")
    assert Decimal(_capital(plan)["cash_after"]) == Decimal("3100.00")


async def test_weight_of_an_unavailable_asset_goes_to_cash(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Вес актива без лота уходит в деньги, а не другим активам.

    Перераспределение выглядит заботой, а на деле меняет правило: доли
    оставшихся перестают быть теми, что проверяло исследование.
    """
    await seed_market(db_session, lots={"EQ_AST_LKOH": None})
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    excluded: list[dict[str, object]] = plan["excluded"]  # type: ignore[assignment]
    assert [row["asset_id"] for row in excluded] == ["EQ_AST_LKOH"]
    assert excluded[0]["reason"] == "не известен размер лота"

    # У SBER по-прежнему один лот, а не два: доля LKOH ушла в деньги.
    assert _row(plan, "SBER")["target_lots"] == 1
    assert Decimal(_capital(plan)["allocated"]) == Decimal("91900.00")
    assert Decimal(_capital(plan)["cash_after"]) == Decimal("8100.00")


async def test_fee_is_charged_on_both_sides(db_session: AsyncSession, settings: Settings) -> None:
    """Комиссия применяется и к покупке, и к продаже.

    Оборот берётся по модулю разницы: продажа стоит столько же, сколько
    покупка на ту же сумму, и учёт одной стороны занизил бы издержки вдвое.
    """
    await seed_market(db_session)
    # Позиция вдвое больше целевой: её часть придётся продать, и эта продажа
    # обязана попасть в оборот наравне с покупками.
    await seed_account(
        db_session,
        cash=Decimal("94000.00"),
        positions=(position("SBER", Decimal("20"), Decimal("300.00")),),
    )
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    positions: list[dict[str, object]] = plan["positions"]  # type: ignore[assignment]
    deltas = [Decimal(str(row["delta_value"])) for row in positions]
    assert any(delta < 0 for delta in deltas), "продажи в плане нет — проверять нечего"

    turnover = sum(abs(delta) for delta in deltas)
    expected = (turnover * Decimal("0.04") / Decimal(100)).quantize(Decimal("0.01"))
    assert Decimal(_capital(plan)["fee_total"]) == expected


async def test_difference_to_current_positions_is_shown(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Разница к текущим позициям — в штуках и в деньгах."""
    await seed_market(db_session)
    await seed_account(
        db_session,
        cash=Decimal("40000.00"),
        positions=(position("SBER", Decimal("200"), Decimal("300.00")),),
    )
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    sber = _row(plan, "SBER")
    target = Decimal(str(sber["target_quantity"]))
    assert Decimal(str(sber["current_quantity"])) == Decimal("200")
    assert Decimal(str(sber["delta_quantity"])) == target - Decimal("200")
    assert Decimal(str(sber["delta_value"])) == (target - Decimal("200")) * Decimal("300.00")


async def test_ranked_asset_outside_the_selection_is_sold_not_ignored(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Бумага вне выборки правила — не «вне вселенной модели».

    Модель её ранжировала, просто не в первых двадцати: целевой вес ноль, и
    план обязан сказать «продать». Считать её неприкосновенной неверно дважды —
    она не попадёт в расчётный капитал и молча останется в портфеле. Найдено на
    живом стенде: три настоящие акции числились наравне с ОФЗ.
    """
    await seed_market(db_session)
    # GAZP ранжирован последним из двадцати одного: в выборку не попадает.
    order = (*(a for a in ALL_ASSETS if a != "EQ_AST_GAZP"), "EQ_AST_GAZP")
    await seed_account(
        db_session,
        cash=Decimal("74000.00"),
        positions=(position("GAZP", Decimal("200"), Decimal("130.00")),),
    )
    await seed_ranking(db_session, settings, order=order)

    plan = await _plan(db_session, settings)

    untouched: list[dict[str, object]] = plan["untouched"]  # type: ignore[assignment]
    assert [row["ticker"] for row in untouched] == []

    gazp = _row(plan, "GAZP")
    assert gazp["target_quantity"] == "0"
    assert Decimal(str(gazp["delta_quantity"])) == Decimal("-200")

    # Её стоимость вошла в расчётный капитал: бумагу можно продать.
    assert Decimal(_capital(plan)["eligible"]) == Decimal("100000.00")


async def test_sells_come_before_buys(db_session: AsyncSession, settings: Settings) -> None:
    """Продажи идут первыми.

    Покупки оплачиваются выручкой от продаж, и список, начинающийся с покупок,
    читается как руководство к действию, которое невыполнимо: денег на них ещё
    нет.
    """
    await seed_market(db_session)
    order = (*(a for a in ALL_ASSETS if a != "EQ_AST_GAZP"), "EQ_AST_GAZP")
    await seed_account(
        db_session,
        cash=Decimal("74000.00"),
        positions=(position("GAZP", Decimal("200"), Decimal("130.00")),),
    )
    await seed_ranking(db_session, settings, order=order)

    plan = await _plan(db_session, settings)

    positions: list[dict[str, object]] = plan["positions"]  # type: ignore[assignment]
    deltas = [Decimal(str(row["delta_value"])) for row in positions]
    first_buy = next((i for i, d in enumerate(deltas) if d > 0), len(deltas))
    last_sell = max((i for i, d in enumerate(deltas) if d < 0), default=-1)

    assert last_sell < first_buy, "покупка показана раньше продажи"


async def test_plan_does_not_rebuild_the_dataset(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Расчёт плана не пересобирает набор, когда сбора после прогона не было.

    Пересборка стоит секунд процессорного времени и держит весь процесс: на
    живом стенде расчёт плана занимал пятнадцать секунд, и всё это время не
    отвечали ни портфель, ни состояние, ни проверка живости.
    """
    from financial_ai.ranking import dataset as dataset_module

    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    async def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("расчёт плана пересобирает набор — это кладёт backend-api")

    monkeypatch.setattr(dataset_module, "build_dataset", forbidden)

    plan = await _plan(db_session, settings)

    assert plan["policy_title"] == "Равные доли · первые 20"


async def test_bonds_and_funds_are_not_touched(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Облигации планом не затрагиваются и в расчётный капитал не входят.

    Это утверждение, а не умолчание: инструмент называется в перечне, чтобы
    его отсутствие в плане нельзя было принять за забывчивость.
    """
    await seed_market(db_session)
    await seed_account(
        db_session,
        positions=(position("SU26238RMFS4", Decimal("200"), Decimal("500.00"), asset_type="bond"),),
    )
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    untouched: list[dict[str, object]] = plan["untouched"]  # type: ignore[assignment]
    assert [row["ticker"] for row in untouched] == ["SU26238RMFS4"]
    # Стоимость облигаций в расчётный капитал не вошла: он равен деньгам.
    assert Decimal(_capital(plan)["eligible"]) == Decimal("100000.00")


async def test_capital_limit_caps_the_allocation(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Лимит ограничивает распределяемое; нераспределённое остаётся деньгами."""
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings, capital_limit=Decimal("20000.00"))

    assert Decimal(_capital(plan)["eligible"]) == Decimal("100000.00")
    assert Decimal(_capital(plan)["limit"]) == Decimal("20000.00")
    assert Decimal(_capital(plan)["allocated"]) <= Decimal("20000.00")


async def test_price_source_names_the_session(db_session: AsyncSession, settings: Settings) -> None:
    """Цена — закрытие названной сессии, а не текущая котировка."""
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings)

    plan = await _plan(db_session, settings)

    source: dict[str, str] = plan["price_source"]  # type: ignore[assignment]
    assert source["kind"] == "session_close"
    assert source["session_date"] == dt.date(2026, 9, 1).isoformat()


# --- отказы -------------------------------------------------------------------


async def test_no_ranking_is_a_refusal_not_an_empty_plan(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Пустой состав прочитался бы как «продай всё»."""
    await seed_market(db_session)
    await seed_account(db_session)

    with pytest.raises(plan_module.PlanError) as error:
        await _plan(db_session, settings)

    assert error.value.code == "no_successful_ranking"


async def test_stale_ranking_is_refused(db_session: AsyncSession, settings: Settings) -> None:
    """Решение относится к другим данным — считать по нему нельзя.

    Вход меняется так, как он меняется в жизни: после прогона что-то собрали.
    Подменить один дайджест недостаточно — дешёвая проверка устаревания
    справедливо ответит «собирать было нечего, вход тот же».
    """
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings, digest="sha256:другой-вход")

    # Сбор после прогона: теперь дайджест обязаны сравнить честно.
    now = dt.datetime.now(dt.UTC)
    await MarketDataRepository(db_session).record_run(
        run_id="после-прогона",
        source_id="equity_d1",
        status="ok",
        started_at=now,
        finished_at=now + dt.timedelta(seconds=1),
        session_date=ASOF,
        rows_written=1,
    )
    await db_session.commit()

    with pytest.raises(plan_module.PlanError) as error:
        await _plan(db_session, settings)

    assert error.value.code == "ranking_stale"


async def test_disconnected_account_is_refused(
    db_session: AsyncSession, settings: Settings
) -> None:
    await seed_market(db_session)
    await seed_ranking(db_session, settings)

    with pytest.raises(plan_module.PlanError) as error:
        await _plan(db_session, settings)

    assert error.value.code == "broker_not_connected"


async def test_stale_snapshot_is_refused(db_session: AsyncSession, settings: Settings) -> None:
    """Расчёт по устаревшему остатку вводит в заблуждение."""
    await seed_market(db_session)
    await seed_account(db_session, captured_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2))
    await seed_ranking(db_session, settings)

    with pytest.raises(plan_module.PlanError) as error:
        await _plan(db_session, settings)

    assert error.value.code == "portfolio_stale"


async def test_insufficient_assets_names_required_and_available(
    db_session: AsyncSession, settings: Settings
) -> None:
    await seed_market(db_session)
    await seed_account(db_session)
    await seed_ranking(db_session, settings, order=("EQ_AST_SBER", "EQ_AST_LKOH"))

    with pytest.raises(plan_module.PlanError) as error:
        await _plan(db_session, settings)

    assert error.value.code == "insufficient_assets"
    assert error.value.details == {"required": 20, "available": 2}


async def test_unknown_policy_is_refused(db_session: AsyncSession, settings: Settings) -> None:
    with pytest.raises(plan_module.PlanError) as error:
        await _plan(db_session, settings, policy="equal_top7")

    assert error.value.code == "unknown_policy"
