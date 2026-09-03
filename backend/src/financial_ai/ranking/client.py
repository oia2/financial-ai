"""Клиент звена ранжирования Daily ML.

Запрос содержит дату решения, ссылку на неизменяемый набор с дайджестом и
перечень активов, данные которых в набор вошли. Сами ряды не передаются.

Кто решает, какие активы попадут в ранжирование, — **сторона модели**: правило
допустимости строится тем же конвейером, что и признаки, и живёт вместе с ним.
Отправитель за модель ничего не решает.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx

from financial_ai.config import Settings
from financial_ai.ranking.dataset import Dataset

logger = logging.getLogger(__name__)


class RankingUnavailableError(RuntimeError):
    """Звено ранжирования недоступно или ответило некорректно.

    Отдельный тип нужен, чтобы сбой ранжирования не смешивался со сбоем сбора
    данных: это разные неисправности с разными последствиями.
    """


@dataclass(frozen=True, slots=True)
class RankingItem:
    """Одна позиция ранжирования."""

    rank: int
    asset_id: str
    price_series_id: str
    score: Decimal


@dataclass(frozen=True, slots=True)
class Ranking:
    """Ответ звена ранжирования."""

    asof_date: dt.date
    model_id: str
    input_digest: str
    emulated: bool
    items: list[RankingItem]
    excluded: list[dict[str, str]]

    @property
    def included_asset_count(self) -> int:
        return len(self.items)


def build_request(dataset: Dataset) -> dict[str, object]:
    """Тело запроса по contracts/daily-ml-request.md."""
    return {
        "asof_date": dataset.asof_date.isoformat(),
        "dataset": {
            "ref": dataset.ref,
            "digest": dataset.digest,
            "windows": dataset.windows,
            # Присутствует всегда: пустой перечень — значимое утверждение
            # «окно полно». Без него неполнота входа стала бы неотличима от
            # решения модели, и выпавший актив выглядел бы исключённым ею.
            "incomplete": dataset.incomplete,
        },
        "assets": [
            {"asset_id": a.asset_id, "price_series_id": a.price_series_id} for a in dataset.assets
        ],
    }


async def request_ranking(
    settings: Settings, dataset: Dataset, client: httpx.AsyncClient | None = None
) -> Ranking:
    """Получить ранжирование на дату решения."""
    payload = build_request(dataset)
    url = f"{settings.daily_ml_url.rstrip('/')}/rankings"

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.daily_ml_timeout_seconds)
    try:
        try:
            response = await http.post(url, json=payload)
        except httpx.HTTPError as error:
            raise RankingUnavailableError(f"звено ранжирования недоступно: {error}") from error

        if response.status_code != httpx.codes.OK:
            raise RankingUnavailableError(
                f"звено ранжирования ответило {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as error:
            raise RankingUnavailableError(f"ответ не является JSON: {error}") from error
    finally:
        if owns_client:
            await http.aclose()

    return _parse(body, dataset)


def _parse(body: dict[str, object], dataset: Dataset) -> Ranking:
    digest = str(body.get("input_digest", ""))
    if digest != dataset.digest:
        # Иначе ранжирование могло бы относиться к другим данным, и доказать
        # обратное постфактум было бы нечем.
        raise RankingUnavailableError(
            f"дайджест в ответе не совпадает с отправленным: {digest} вместо {dataset.digest}"
        )

    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        raise RankingUnavailableError("в ответе нет списка items")

    items = [
        RankingItem(
            rank=int(item["rank"]),
            asset_id=str(item["asset_id"]),
            price_series_id=str(item["price_series_id"]),
            # Скор приходит строкой и остаётся точным: float его исказил бы.
            score=Decimal(str(item["score"])),
        )
        for item in raw_items
        if isinstance(item, dict)
    ]

    known = {a.asset_id for a in dataset.assets}
    unexpected = [i.asset_id for i in items if i.asset_id not in known]
    if unexpected:
        raise RankingUnavailableError(
            f"в ответе активы, которых не было в запросе: {', '.join(sorted(unexpected)[:5])}"
        )

    excluded_raw = body.get("excluded")
    excluded = (
        [
            {"asset_id": str(e.get("asset_id", "")), "reason": str(e.get("reason", ""))}
            for e in excluded_raw
            if isinstance(e, dict)
        ]
        if isinstance(excluded_raw, list)
        else []
    )

    return Ranking(
        asof_date=dataset.asof_date,
        model_id=str(body.get("model_id", "")),
        input_digest=digest,
        emulated=bool(body.get("emulated", False)),
        items=items,
        excluded=excluded,
    )
