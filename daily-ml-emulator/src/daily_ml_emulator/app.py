"""HTTP-интерфейс эмулятора Daily ML.

Эмулятор изображает работу звена ранжирования: отвечает на запрос правдоподобным
по форме ответом. Ни модели, ни рыночных данных, ни вычислений за этим нет —
см. `ranking.py`.

Контракт запроса — тот же, который будет принимать обученная модель: она встанет
в контейнере на это место. Набор входных данных передаётся **ссылкой с
дайджестом**, а не рядами в теле: окно в 314 сессий по 288 активам — это около
шести мегабайт, из которых новой является одна сессия из 314.

Сам эмулятор набор не читает: он ничего не вычисляет. Ссылку он принимает,
проверяет её наличие и возвращает дайджест обратно — чтобы пара «запрос — ответ»
была сопоставима постфактум.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from daily_ml_emulator.config import Settings, load_settings
from daily_ml_emulator.ranking import build_ranking
from daily_ml_emulator.universe import UniverseError, entries_from_request

logger = logging.getLogger("daily_ml_emulator")

EMULATION_NOTICE = (
    "Значения вымышлены эмулятором Daily ML. Модели, рыночных данных и вычислений за ними нет."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Загрузить конфигурацию до приёма первого запроса.

    Вселенная больше не конфигурируется: активы приходят в запросе.
    """
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())
    app.state.settings = settings
    logger.info("эмулятор готов, модель %s", settings.daily_ml_emulator_model_id)
    yield


app = FastAPI(
    title="Daily ML Emulator",
    version="0.2.0",
    summary="Заглушка звена ранжирования активов",
    description=(
        "**Это эмулятор.** Все скоры вымышлены и получены циклическим сдвигом "
        "переданного списка активов. Никакой модели, никаких рыночных данных и "
        "никаких вычислений за ними нет.\n\n"
        "Каждый успешный ответ содержит `emulated: true` — потребитель обязан это "
        "проверять, пока звено ранжирования не заменено настоящей моделью.\n\n"
        "Контракт запроса — будущий контракт обученной модели: она встанет в "
        "контейнере на это место. Набор входных данных передаётся ссылкой с "
        "дайджестом; эмулятор его не читает, настоящая модель будет."
    ),
    lifespan=lifespan,
)


class DatasetRef(BaseModel):
    """Ссылка на неизменяемый набор входных данных."""

    ref: str = Field(description="Ссылка на набор")
    digest: str = Field(description="Дайджест содержимого набора")
    windows: dict[str, int] = Field(
        default_factory=dict,
        description="Фактические глубины окон, вошедшие в набор, в торговых сессиях",
    )


class AssetRef(BaseModel):
    """Актив, данные которого вошли в набор."""

    asset_id: str = Field(description="Экономический актив, устойчивый к переименованиям тикера")
    price_series_id: str = Field(description="Сшиваемый ценовой ряд этого актива")


class RankingRequest(BaseModel):
    """Запрос ранжирования на дату решения."""

    asof_date: date = Field(description="Дата решения — последняя завершённая торговая сессия")
    dataset: DatasetRef = Field(description="Ссылка на набор входных данных")
    assets: list[AssetRef] = Field(description="Активы, данные которых вошли в набор")


class RankingItemResponse(BaseModel):
    """Одна позиция ранжирования."""

    rank: int = Field(description="Место в порядке убывания скора; 1 — лучший")
    asset_id: str = Field(description="Экономический актив")
    price_series_id: str = Field(description="Сшиваемый ценовой ряд этого актива")
    score: str = Field(
        description=(
            "Кросс-секционный ранжирующий скор внутри даты решения. Передаётся строкой, "
            "чтобы значение у потребителя точно совпадало с выданным. Осмыслен только "
            "порядок: абсолютное значение интерпретации не имеет."
        )
    )


class ExcludedAsset(BaseModel):
    """Актив, переданный в запросе, но не вошедший в ранжирование."""

    asset_id: str
    reason: str


class RankingResponse(BaseModel):
    """Ранжирование на одну дату решения."""

    asof_date: date = Field(description="Дата решения из запроса")
    model_id: str = Field(description="Идентификатор модели, выдавшей ранжирование")
    input_digest: str = Field(
        description="Дайджест набора из запроса, повторённый в ответе: делает пару "
        "«запрос — ответ» сопоставимой постфактум"
    )
    generated_at: str = Field(description="Момент формирования ответа, UTC")
    emulated: bool = Field(description="Всегда true. Машиночитаемый признак эмуляции")
    emulation_notice: str = Field(description="Человекочитаемое предупреждение")
    included_asset_count: int = Field(description="Сколько активов вошло в ранжирование")
    excluded: list[ExcludedAsset] = Field(description="Переданные, но не вошедшие активы")
    items: list[RankingItemResponse] = Field(
        description="Позиции ранжирования, упорядоченные по возрастанию rank"
    )


class HealthResponse(BaseModel):
    """Готовность принимать запросы."""

    status: str


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@app.post(
    "/rankings",
    response_model=RankingResponse,
    summary="Ранжирование активов на дату решения",
    description=(
        "Возвращает ранжирование активов, переданных в запросе.\n\n"
        "Какие активы допустимы к ранжированию, решает **сторона модели**: это "
        "правило строится тем же конвейером, что и признаки, и живёт вместе с ним. "
        "Отправитель передаёт активы, данные которых у него есть, и за модель "
        "ничего не решает.\n\n"
        "Эмулятор набор по ссылке не читает — он ничего не вычисляет. Настоящая "
        "модель будет: тогда часть переданных активов может оказаться в `excluded`."
    ),
)
def post_rankings(payload: RankingRequest, request: Request) -> RankingResponse:
    """Собрать ответ по правилу из `ranking.py`."""
    settings = get_settings(request)

    entries = entries_from_request(
        [{"asset_id": a.asset_id, "price_series_id": a.price_series_id} for a in payload.assets]
    )
    items = build_ranking(entries, payload.asof_date)

    return RankingResponse(
        asof_date=payload.asof_date,
        model_id=settings.daily_ml_emulator_model_id,
        input_digest=payload.dataset.digest,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        emulated=True,
        emulation_notice=EMULATION_NOTICE,
        included_asset_count=len(items),
        # Эмулятор ранжирует всё переданное: отбраковывать не по чему, он не
        # читает набор. У настоящей модели этот список может быть непустым.
        excluded=[],
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
)
def get_health() -> HealthResponse:
    """Проверка живости для healthcheck'а docker compose."""
    return HealthResponse(status="ok")


@app.exception_handler(UniverseError)
async def handle_universe_error(request: Request, exc: UniverseError) -> JSONResponse:
    """Непригодный перечень активов — ошибка запроса, а не сбой сервиса."""
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "invalid_request", "message": str(exc)}},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Привести ошибки валидации к единой форме проекта.

    Форма `{"detail": {"code": ..., "message": ...}}` зафиксирована контрактом
    первой фичи; второго соглашения проект не вводит.
    """
    fields = {str(part) for error in exc.errors() for part in error.get("loc", ())}

    if "asof_date" in fields:
        code = "invalid_asof_date"
        message = "asof_date должен быть датой в формате YYYY-MM-DD"
    else:
        code = "invalid_request"
        message = "запрос не прошёл валидацию: требуются asof_date, dataset и assets"

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
