"""Расчёт плана портфеля.

План — утверждение о желаемом составе счёта, полученное применением правила
распределения к последнему успешному ранжированию и текущему состоянию счёта.
**Портфель он не меняет, заявок не выставляет и к брокеру не обращается**: все
входы уже в хранилище, и расчёт — чистая арифметика над ними.

Пять правил, каждое перенесено из ребалансировки исследования и каждое легко
нарушить незаметно:

1. **Инструменты вне вселенной модели не затрагиваются.** Облигации и денежный
   фонд переносятся как есть и в расчётный капитал не входят.
2. **Количество кратно лоту.** На бирже торгуют лотами, и дробное количество
   неисполнимо. Это единственное правило, которого в исследовании нет: там
   акции дробные, лот появляется только в модуле исполнения — и там он
   обязательный аргумент.
3. **Остаток округления остаётся деньгами.** Между активами он не
   перераспределяется: иначе доли исказятся относительно правила.
4. **Вес недоступного актива уходит в деньги.** В коде ребалансировки это
   записано явным комментарием. Перераспределение выглядит заботой, а на деле
   меняет правило.
5. **Комиссия применяется к каждой стороне** — и к покупке, и к продаже.

Отказ — это отсутствие плана, а не пустой план: показать пустой состав значило
бы сказать «продай всё».
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import Settings
from financial_ai.daily_ml import readiness
from financial_ai.daily_ml.models import DailyMlRankingItem, DailyMlRun
from financial_ai.daily_ml.repository import DailyMlRepository
from financial_ai.db import repository as portfolio_repository
from financial_ai.db.models import AccountState, PortfolioPosition
from financial_ai.db.settings_repo import get_interval_seconds
from financial_ai.domain import portfolio as portfolio_domain
from financial_ai.market_data.models import MarketAsset
from financial_ai.market_data.repository import MarketDataRepository
from financial_ai.portfolio_plan import policies

ZERO = Decimal(0)
HUNDRED = Decimal(100)

# Комиссия за одну сторону по умолчанию: половина round-trip издержек 0,0008 из
# диагностики исследования.
DEFAULT_FEE_PERCENT = Decimal("0.04")
MAX_FEE_PERCENT = Decimal("5")

# Тип инструмента, который брокер присваивает акциям. Всё остальное — облигации,
# фонды, валюта — вселенной модели не принадлежит.
SHARE_TYPE = "share"


class PlanError(Exception):
    """Плана нет, и названа причина.

    Код отличает случаи друг от друга: «ранжирования не было» и «ранжирование
    устарело» требуют разного ожидания, и подмена одного другим отправляет
    человека ждать не того.
    """

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Настройки расчёта, заданные человеком."""

    policy: str
    capital_limit: Decimal | None = None
    fee_percent: Decimal = DEFAULT_FEE_PERCENT


@dataclass
class _Holding:
    """Текущая позиция по активу вселенной модели."""

    quantity: Decimal
    value: Decimal
    name: str | None = None


@dataclass
class _Plan:
    positions: list[dict[str, object]] = field(default_factory=list)
    untouched: list[dict[str, object]] = field(default_factory=list)
    excluded: list[dict[str, object]] = field(default_factory=list)


def parse_decimal(raw: object, code: str, message: str) -> Decimal:
    """Разобрать денежную величину из запроса.

    Строкой, а не `float`: правило проекта действует на всём пути «источник →
    БД → API → JSON», и вход в него входит.
    """
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError) as error:
        raise PlanError(code, message) from error


async def build(
    session: AsyncSession, settings: Settings, request: PlanRequest
) -> dict[str, object]:
    """Посчитать план. Бросает `PlanError`, если считать нечего или не на чем."""
    policy = policies.get(request.policy)
    if policy is None:
        raise PlanError("unknown_policy", "правила с таким идентификатором нет")

    if request.fee_percent < ZERO or request.fee_percent > MAX_FEE_PERCENT:
        raise PlanError("invalid_fee", "комиссия вне допустимого диапазона")
    if request.capital_limit is not None and request.capital_limit <= ZERO:
        raise PlanError("invalid_limit", "лимит распределения должен быть больше нуля")

    run = await _ranking(session, settings)
    ranked = await _ranked_items(session, run.id, policy.asset_count)

    if len(ranked) < policy.asset_count:
        raise PlanError(
            "insufficient_assets",
            "в ранжировании меньше активов, чем требует правило",
            required=policy.asset_count,
            available=len(ranked),
        )

    state, positions = await _account(session)

    price_session, prices = await _prices(session)

    # Вселенная — вся выдача модели; веса задаёт правило только для выборки.
    universe = await _universe(session, run.id)
    assets = await _assets(session, list(universe))

    holdings, untouched = _split_positions(positions, assets, universe)
    eligible = state.cash + sum((holding.value for holding in holdings.values()), ZERO)
    limit = min(eligible, request.capital_limit) if request.capital_limit else eligible

    plan = _positions(policy, ranked, assets, prices, holdings, limit)
    plan.untouched = untouched
    _close_out(plan, ranked, universe, assets, prices, holdings)

    # Продажи идут первыми. Порядок здесь не оформление: покупки оплачиваются
    # выручкой от продаж, и список, начинающийся с покупок, читается как
    # руководство к действию, которое невыполнимо — денег на них ещё нет.
    plan.positions.sort(key=lambda row: (_money(row["delta_value"]), row["rank"]))

    allocated = sum((_money(row["target_value"]) for row in plan.positions), ZERO)
    turnover = sum((abs(_money(row["delta_value"])) for row in plan.positions), ZERO)
    fee_total = (turnover * request.fee_percent / HUNDRED).quantize(Decimal("0.01"))

    # Доля позиции считается от всего счёта — вместе с деньгами, облигациями и
    # фондом. Считает сервер: делить строки десятичных дробей в браузере
    # означало бы разбирать их в `float`, а это ровно то, что на этом пути
    # запрещено.
    untouched_value = sum((_money(row["value"]) for row in plan.untouched), ZERO)
    account_total = allocated + (limit - allocated) + untouched_value
    for row in (*plan.positions, *plan.untouched):
        amount = _money(row["target_value"] if "target_value" in row else row["value"])
        row["weight_after"] = _text(amount / account_total) if account_total > ZERO else "0"

    return {
        "asof_date": run.asof_date.isoformat(),
        "run_id": run.id,
        "policy": policy.id,
        # Название правила отдаётся рядом с идентификатором: `equal_top20` это
        # ключ для машины, а человеку на экране нужно «Равные доли · первые 20».
        "policy_title": policy.title,
        # Ранжирование получено эмулятором: план построен на вымышленных
        # скорах, и сказать об этом обязан тот, кто план отдаёт.
        "emulated": run.emulated,
        # Цена — закрытие названной сессии, а не текущая котировка. Дата стоит
        # рядом с ценой всегда: без неё одно принимается за другое.
        "price_source": {"kind": "session_close", "session_date": price_session.isoformat()},
        "capital": {
            "eligible": _text(eligible),
            "limit": _text(limit),
            "allocated": _text(allocated),
            # Всё нераспределённое — деньги: и остаток округления, и вес
            # актива, который в план не попал.
            "cash_after": _text(limit - allocated),
            "fee_total": _text(fee_total),
            # Весь счёт: от него считаются доли позиций после плана.
            "account_total": _text(account_total),
        },
        "positions": plan.positions,
        "untouched": plan.untouched,
        "excluded": plan.excluded,
    }


async def _ranking(session: AsyncSession, settings: Settings) -> DailyMlRun:
    run = await DailyMlRepository(session).latest_success()
    if run is None:
        raise PlanError(
            "no_successful_ranking",
            "успешного ранжирования ещё не было: дождитесь первого прогона",
        )

    # `since` — окончание прогона: если после него ничего не собирали, вход
    # измениться не мог, и пересобирать набор ради дайджеста не нужно. Без этого
    # расчёт плана стоил семнадцати секунд и держал весь процесс.
    if await readiness.is_stale(
        session,
        settings,
        run.asof_date,
        run.dataset_digest,
        since=run.finished_at,
        window=(run.window_from, run.window_till),
    ):
        raise PlanError(
            "ranking_stale",
            "вход последнего ранжирования изменился: решение относится к другим данным",
        )

    return run


async def _ranked_items(session: AsyncSession, run_id: int, count: int) -> list[DailyMlRankingItem]:
    rows = await session.execute(
        select(DailyMlRankingItem)
        .where(DailyMlRankingItem.run_id == run_id)
        .order_by(DailyMlRankingItem.rank)
        .limit(count)
    )
    return list(rows.scalars())


async def _universe(session: AsyncSession, run_id: int) -> dict[str, int]:
    """Вселенная модели: **все** активы выдачи, а не выборка правила.

    Разница существенная. Бумага на 57-м месте моделью ранжирована, просто не
    попала в первые двадцать: её целевой вес — ноль, и план обязан сказать
    «продать». Считать её «вне вселенной модели» неверно дважды — она не
    попадает в расчётный капитал и остаётся в портфеле молча. Наблюдалось на
    живом стенде: три настоящие акции числились неприкосновенными наравне с
    ОФЗ.
    """
    rows = await session.execute(
        select(DailyMlRankingItem.asset_id, DailyMlRankingItem.rank).where(
            DailyMlRankingItem.run_id == run_id
        )
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def _account(session: AsyncSession) -> tuple[AccountState, list[PortfolioPosition]]:
    account = await portfolio_repository.get_account(session)
    state = await portfolio_repository.get_state(session)
    if account is None or state is None:
        raise PlanError(
            "broker_not_connected",
            "счёт не подключён: деньги и позиции неизвестны",
        )

    interval = await get_interval_seconds(session)
    if portfolio_domain.is_stale(state.captured_at, dt.datetime.now(dt.UTC), interval):
        raise PlanError(
            "portfolio_stale",
            "снимок счёта устарел: обновите состояние портфеля",
        )

    return state, await portfolio_repository.get_positions(session)


async def _prices(session: AsyncSession) -> tuple[dt.date, dict[str, Decimal]]:
    """Закрытия последней торговой сессии, по активам."""
    repository = MarketDataRepository(session)
    last = await repository.latest_trading_session()
    if last is None:
        raise PlanError("no_prices", "цен закрытия нет: рыночные данные не собраны")

    # Сессия могла закрыться, а данные за неё ещё не прийти. Берётся последняя
    # сессия, по которой закрытия действительно есть: цена без даты — не цена.
    window = await repository.previous_sessions(last, 10)
    collected = await repository.sessions_with_daily_bars(window)
    if not collected:
        raise PlanError("no_prices", "цен закрытия нет: рыночные данные не собраны")

    session_date = max(collected)
    bars = await repository.daily_bars_for_window([session_date])
    prices = {bar.asset_id: bar.close for bar in bars if bar.close is not None}
    return session_date, prices


async def _assets(session: AsyncSession, asset_ids: list[str]) -> dict[str, MarketAsset]:
    rows = await session.execute(select(MarketAsset).where(MarketAsset.asset_id.in_(asset_ids)))
    return {asset.asset_id: asset for asset in rows.scalars()}


def _split_positions(
    positions: list[PortfolioPosition],
    assets: dict[str, MarketAsset],
    universe: dict[str, int],
) -> tuple[dict[str, _Holding], list[dict[str, object]]]:
    """Разделить позиции на вселенную модели и всё остальное.

    Вселенная — это **вся выдача модели**, а не выборка правила: бумага, не
    попавшая в первые двадцать, моделью всё равно ранжирована, и её целевой вес
    — ноль, а не «не трогать».

    Облигации и денежный фонд планом не затрагиваются — это утверждение, а не
    умолчание, поэтому они перечисляются, а не молча пропускаются.
    """
    by_ticker = {
        asset.ticker: asset_id for asset_id, asset in assets.items() if asset_id in universe
    }

    holdings: dict[str, _Holding] = {}
    untouched: list[dict[str, object]] = []

    for position in positions:
        asset_id = by_ticker.get(position.ticker or "")
        if position.asset_type == SHARE_TYPE and asset_id is not None:
            holdings[asset_id] = _Holding(position.quantity, position.value, position.name)
            continue

        untouched.append(
            {
                "ticker": position.ticker,
                "name": position.name,
                "quantity": _text(position.quantity),
                # Стоимость нужна, чтобы доля позиции считалась от всего счёта,
                # а не от одних акций: облигации в счёте есть, и доля без них
                # была бы больше настоящей.
                "value": _text(position.value),
                "reason": "вне вселенной модели",
            }
        )

    return holdings, untouched


def _positions(
    policy: policies.Policy,
    ranked: list[DailyMlRankingItem],
    assets: dict[str, MarketAsset],
    prices: dict[str, Decimal],
    holdings: dict[str, _Holding],
    limit: Decimal,
) -> _Plan:
    plan = _Plan()

    for item, weight in zip(ranked, policy.weights(), strict=True):
        asset = assets.get(item.asset_id)
        price = prices.get(item.asset_id)
        lot = asset.lot_size if asset is not None else None

        reason = _why_excluded(asset, price, lot)
        if reason is not None:
            # Вес такого актива уходит в деньги, а не перераспределяется между
            # остальными: перераспределение исказило бы доли относительно
            # правила (FR-067).
            plan.excluded.append({"asset_id": item.asset_id, "rank": item.rank, "reason": reason})
            continue
        assert asset is not None and price is not None and lot is not None

        target_value = limit * weight
        lot_cost = price * Decimal(lot)
        lots = int((target_value / lot_cost).to_integral_value(rounding=ROUND_DOWN))
        quantity = Decimal(lots * lot)

        holding = holdings.get(item.asset_id)
        current = holding.quantity if holding else ZERO

        plan.positions.append(
            {
                "asset_id": item.asset_id,
                "ticker": asset.ticker,
                "name": holding.name if holding else None,
                "rank": item.rank,
                "target_weight": _text(weight),
                "price": _text(price),
                "lot_size": lot,
                "target_lots": lots,
                "target_quantity": _text(quantity),
                "target_value": _text(quantity * price),
                "current_quantity": _text(current),
                "delta_quantity": _text(quantity - current),
                "delta_value": _text((quantity - current) * price),
            }
        )

    return plan


def _why_excluded(asset: MarketAsset | None, price: Decimal | None, lot: int | None) -> str | None:
    """Почему актив в план не попал.

    Причина называется своя: «нет лота» и «нет цены» чинятся разными способами,
    и общее «недоступен» не сказало бы, чего ждать (FR-069).
    """
    if asset is None:
        return "актив не найден в справочнике"
    if lot is None or lot <= 0:
        return "не известен размер лота"
    if price is None or price <= ZERO:
        return "не известна цена закрытия последней сессии"
    return None


def _close_out(
    plan: _Plan,
    ranked: list[DailyMlRankingItem],
    universe: dict[str, int],
    assets: dict[str, MarketAsset],
    prices: dict[str, Decimal],
    holdings: dict[str, _Holding],
) -> None:
    """Позиции вселенной, не попавшие в выборку правила, закрываются.

    Целевой состав — это выборка правила; всё остальное из вселенной должно
    уйти. Оставить такую позицию без строки значило бы умолчать о продаже,
    которая правилу необходима.
    """
    selected = {item.asset_id for item in ranked}

    for asset_id, holding in holdings.items():
        if asset_id in selected or holding.quantity == ZERO:
            continue

        asset = assets.get(asset_id)
        price = prices.get(asset_id)
        if asset is None or price is None or price <= ZERO:
            # Продажу без цены закрытия не оценить. Позиция остаётся у
            # человека, и причина названа, а не спрятана.
            plan.excluded.append(
                {
                    "asset_id": asset_id,
                    "rank": universe.get(asset_id, 0),
                    "reason": "не известна цена закрытия последней сессии",
                }
            )
            continue

        plan.positions.append(
            {
                "asset_id": asset_id,
                "ticker": asset.ticker,
                "name": holding.name,
                "rank": universe.get(asset_id, 0),
                "target_weight": "0",
                "price": _text(price),
                "lot_size": asset.lot_size,
                "target_lots": 0,
                "target_quantity": "0",
                "target_value": _text(ZERO),
                "current_quantity": _text(holding.quantity),
                "delta_quantity": _text(-holding.quantity),
                "delta_value": _text(-holding.quantity * price),
            }
        )


def _money(value: object) -> Decimal:
    return Decimal(str(value))


def _text(value: Decimal) -> str:
    """Число строкой: `float` на пути «БД → API → JSON» теряет копейки."""
    return format(value, "f")
