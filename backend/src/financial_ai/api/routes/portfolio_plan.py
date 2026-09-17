"""Публичная граница раздела «План портфеля».

Считает `backend-api`: все входы уже в хранилище — сохранённое ранжирование,
состояние счёта, цены закрытия, размеры лотов, — и внешних обращений расчёт не
делает. Граница проекта остаётся прежней: worker ходит наружу, api считает по
сохранённому.

**Маршрутов исполнения здесь нет и не появится.** Расчёт не меняет портфель, не
выставляет заявок и не обращается к брокеру (FR-063, FR-071).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session
from financial_ai.portfolio_plan import plan as plan_module
from financial_ai.portfolio_plan import policies

router = APIRouter(tags=["portfolio-plan"])

# Отказы расчёта: «нечего считать» и «неверно задано» — разные вещи, и
# состояние ответа обязано их различать.
CONFLICTS = {
    "no_successful_ranking",
    "ranking_stale",
    "broker_not_connected",
    "portfolio_stale",
    "no_prices",
}


class PlanRequestIn(BaseModel):
    """Настройки расчёта.

    Денежные величины приходят строками: `float` на пути «вход → расчёт → JSON»
    теряет копейки, а лимит распределения — это деньги.
    """

    policy: str = Field(description="Идентификатор правила распределения")
    capital_limit: str | None = Field(
        default=None, description="Верхняя граница распределяемого, ₽. По умолчанию — весь капитал"
    )
    fee_percent: str | None = Field(
        default=None, description="Комиссия за одну сторону, %. По умолчанию 0,04"
    )


@router.get("/portfolio-plan/policies")
async def read_policies() -> dict[str, Any]:
    """Правила распределения, перенесённые из исследования.

    Перечень задаётся сервером: произвольное правило человеком не вводится —
    иначе в плане появились бы веса, не проверенные исследованием.
    """
    return {
        "policies": [
            {
                "id": policy.id,
                "title": policy.title,
                "asset_count": policy.asset_count,
                "description": policy.description,
                "source": policy.source,
            }
            for policy in policies.POLICIES
        ],
        "default_policy": policies.DEFAULT_POLICY,
    }


@router.post("/portfolio-plan")
async def calculate_plan(
    payload: PlanRequestIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Посчитать план по последнему успешному ранжированию.

    Расчёт портфель не меняет: это утверждение о желаемом составе счёта.
    """
    # Портфель меняется непрерывно, поэтому сохранённый план устарел бы в
    # момент записи. Ответ не кэшируется по той же причине.
    response.headers["Cache-Control"] = "no-store"

    try:
        limit: Decimal | None = None
        if payload.capital_limit is not None:
            limit = plan_module.parse_decimal(
                payload.capital_limit, "invalid_limit", "лимит распределения не разбирается"
            )

        fee = plan_module.DEFAULT_FEE_PERCENT
        if payload.fee_percent is not None:
            fee = plan_module.parse_decimal(
                payload.fee_percent, "invalid_fee", "комиссия не разбирается"
            )

        # Разбор входа внутри того же блока: неразбираемый лимит — такой же
        # отказ расчёта, как и устаревшее ранжирование, и форма ответа у них
        # обязана быть одна.
        request = plan_module.PlanRequest(
            policy=payload.policy, capital_limit=limit, fee_percent=fee
        )
        return await plan_module.build(session, get_settings(), request)
    except plan_module.PlanError as error:
        # Отказ — это отсутствие плана, а не пустой план: пустой состав
        # прочитался бы как «продай всё».
        raise HTTPException(
            status_code=409 if error.code in CONFLICTS else 422,
            detail={"code": error.code, "message": error.message, **error.details},
        ) from error
