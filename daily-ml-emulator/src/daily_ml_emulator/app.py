"""HTTP-интерфейс эмулятора Daily ML.

Эмулятор изображает работу звена ранжирования активов: отвечает на запрос
правдоподобным по форме ответом. Ни модели, ни рыночных данных, ни вычислений за этим
нет — см. `ranking.py`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from daily_ml_emulator.config import Settings, load_settings
from daily_ml_emulator.ranking import build_ranking
from daily_ml_emulator.universe import UniverseEntry, load_universe

logger = logging.getLogger("daily_ml_emulator")

EMULATION_NOTICE = (
    "Значения вымышлены эмулятором Daily ML. Модели, рыночных данных и вычислений за ними нет."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Загрузить конфигурацию и вселенную до приёма первого запроса.

    Непригодная конфигурация не даёт приложению подняться: исключение из
    `load_universe` прерывает старт.
    """
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())

    universe_path = Path(settings.daily_ml_emulator_universe_path)
    universe = load_universe(universe_path)

    app.state.settings = settings
    app.state.universe = universe
    logger.info("вселенная загружена: %d активов из %s", len(universe), universe_path)
    yield


app = FastAPI(
    title="Daily ML Emulator",
    version="0.1.0",
    summary="Заглушка звена ранжирования активов",
    description=(
        "**Это эмулятор.** Все скоры вымышлены и получены циклическим сдвигом "
        "настроенного списка активов. Никакой модели, никаких рыночных данных и "
        "никаких вычислений за ними нет.\n\n"
        "Каждый успешный ответ содержит `emulated: true` — потребитель обязан это "
        "проверять, пока звено ранжирования не заменено настоящей моделью.\n\n"
        "Форма ответа перенесена из исследовательского репозитория модели: ключ "
        "`decision_date + asset_id`, идентичность актива `asset_id` + "
        "`price_series_id`, кросс-секционный ранжирующий скор внутри даты решения."
    ),
    lifespan=lifespan,
)


class RankingItemResponse(BaseModel):
    """Одна позиция ранжирования."""

    rank: int = Field(description="Место в порядке убывания скора; 1 — лучший")
    asset_id: str = Field(description="Экономический актив, устойчивый к переименованиям тикера")
    price_series_id: str = Field(description="Сшиваемый ценовой ряд этого актива")
    score: str = Field(
        description=(
            "Кросс-секционный ранжирующий скор внутри даты решения. Передаётся строкой, "
            "чтобы значение у потребителя точно совпадало с выданным. Осмыслен только "
            "порядок: абсолютное значение интерпретации не имеет."
        )
    )


class RankingResponse(BaseModel):
    """Ранжирование всей вселенной активов на одну дату решения."""

    decision_date: date = Field(description="Дата решения из запроса")
    model_id: str = Field(description="Идентификатор модели, выдавшей ранжирование")
    generated_at: str = Field(description="Момент формирования ответа, UTC")
    emulated: bool = Field(description="Всегда true. Машиночитаемый признак эмуляции")
    emulation_notice: str = Field(description="Человекочитаемое предупреждение")
    items: list[RankingItemResponse] = Field(
        description="Позиции ранжирования, упорядоченные по возрастанию rank"
    )


class HealthResponse(BaseModel):
    """Готовность принимать запросы."""

    status: str


def get_settings(request: Request) -> Settings:
    """Настройки, загруженные при старте."""
    settings: Settings = request.app.state.settings
    return settings


def get_universe(request: Request) -> tuple[UniverseEntry, ...]:
    """Вселенная активов, загруженная при старте."""
    universe: tuple[UniverseEntry, ...] = request.app.state.universe
    return universe


@app.get(
    "/rankings",
    response_model=RankingResponse,
    summary="Ранжирование активов на дату решения",
    description=(
        "Возвращает ранжирование всей вселенной активов. Вселенной владеет эмулятор — "
        "вызывающая сторона её не передаёт и повлиять на состав не может. Так же "
        "устроена и настоящая модель.\n\n"
        "Торговый календарь MOEX эмулятору неизвестен: он отвечает на любую "
        "синтаксически корректную дату, включая выходные и даты в будущем."
    ),
)
def get_rankings(
    decision_date: Annotated[date, Query(description="Дата решения в формате YYYY-MM-DD")],
    settings: Annotated[Settings, Depends(get_settings)],
    universe: Annotated[tuple[UniverseEntry, ...], Depends(get_universe)],
) -> RankingResponse:
    """Собрать ответ по правилу из `ranking.py`."""
    items = build_ranking(universe, decision_date)
    return RankingResponse(
        decision_date=decision_date,
        model_id=settings.daily_ml_emulator_model_id,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        emulated=True,
        emulation_notice=EMULATION_NOTICE,
        items=[
            RankingItemResponse(
                rank=item.rank,
                asset_id=item.asset_id,
                price_series_id=item.price_series_id,
                score=item.score,
            )
            for item in items
        ],
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Готовность принимать запросы",
    description=(
        "Ответ 200 означает, что вселенная уже загружена и прошла валидацию: "
        "непригодная конфигурация не даёт контейнеру подняться, поэтому «здоровый, но "
        "неработоспособный» эмулятор невозможен."
    ),
)
def get_health() -> HealthResponse:
    """Проверка живости для healthcheck'а docker compose."""
    return HealthResponse(status="ok")


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Привести ошибки валидации к единой форме проекта.

    Форма `{"detail": {"code": ..., "message": ...}}` зафиксирована контрактом первой
    фичи; второго соглашения проект не вводит.
    """
    about_decision_date = any(
        "decision_date" in [str(part) for part in error.get("loc", ())] for error in exc.errors()
    )
    if about_decision_date:
        code = "invalid_decision_date"
        message = "decision_date должен быть датой в формате YYYY-MM-DD"
    else:
        code = "validation_failed"
        message = "запрос не прошёл валидацию"

    return JSONResponse(status_code=422, content={"detail": {"code": code, "message": message}})


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Привести прочие ошибки к той же форме."""
    detail = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "error", "message": str(exc.detail)}
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})
