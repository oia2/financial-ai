"""Частично собранный источник не становится успехом.

Дефект, ради которого написаны эти тесты, выглядел так: `_fetch_series()`
возвращал `{}` и при ошибке транспорта, и при корректном пустом ответе. Обход
рядов шёл дальше, итог складывался из уцелевших, и источник записывался «ок».
Один полученный ряд из пяти закрывал сессию всем пяти — остальные четыре не
попадали больше ни в один план (FR-032, T205).

То же у ЦБ: ошибка кривой уносила с собой весь источник вместе с уже
полученной ставкой. На стенде 2026-09-20 у `CBR_KEY_RATE` оказалось 311
значений против 308 у каждой точки `CBR_ZCYC_*`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from financial_ai.market_data import ingest
from financial_ai.market_data.interrupt import SourcePartialError, SourceStoppedError
from financial_ai.market_data.iss.client import IssError
from financial_ai.market_data.sources import cbr, global_series
from financial_ai.market_data.sources.global_series import SeriesSpec

DAY = dt.date(2026, 8, 28)

SPECS = (
    SeriesSpec("IMOEX", "IMOEX", engine="stock", market="index"),
    SeriesSpec("RTSI", "RTSI", engine="stock", market="index"),
    SeriesSpec("RGBI", "RGBI", engine="stock", market="index"),
    SeriesSpec("RVI", "RVI", engine="stock", market="index"),
    SeriesSpec("USD_ISS", "USD000UTSTOM", engine="currency", market="selt", board="CETS"),
)


def _client(side_effect: object) -> Mock:
    client = Mock()
    client.should_stop = None
    client.fetch_security_history = AsyncMock(side_effect=side_effect)
    return client


def _repository() -> Mock:
    repository = Mock()
    repository.upsert_global_values = AsyncMock(return_value=1)
    return repository


def _row(day: dt.date) -> dict[str, object]:
    return {"SECID": "IMOEX", "TRADEDATE": day.isoformat(), "CLOSE": Decimal("3200.55")}


# --- Глобальные ряды ---------------------------------------------------------


async def test_one_series_of_five_saves_value_but_does_not_close_source() -> None:
    """1 успешный ряд + 4 ошибки: ряд сохранён, источник незавершён."""
    client = _client([[_row(DAY)], IssError("нет ответа"), IssError("нет ответа"),
                      IssError("нет ответа"), IssError("нет ответа")])
    repository = _repository()

    with pytest.raises(SourcePartialError) as raised:
        await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    # Полученное сохранено: остановка записи не отменяет.
    assert raised.value.rows_written == 1
    repository.upsert_global_values.assert_awaited_once()
    # И названо, что именно осталось работой, — а не «данных нет».
    assert raised.value.unfinished == ("RTSI", "RGBI", "RVI", "USD_ISS")
    assert "RTSI" in raised.value.detail


async def test_all_series_failing_is_a_failure_with_nothing_written() -> None:
    """Все ряды упали: сохранять нечего, успехом это не становится."""
    client = _client(IssError("нет ответа"))
    repository = _repository()

    with pytest.raises(SourcePartialError) as raised:
        await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert raised.value.rows_written == 0
    assert len(raised.value.unfinished) == len(SPECS)
    repository.upsert_global_values.assert_not_awaited()


async def test_all_series_succeeding_closes_the_source() -> None:
    """Вся применимая работа обработана — источник закрыт, исключения нет."""
    client = _client([[_row(DAY)] for _ in SPECS])
    repository = _repository()

    written = await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert written == len(SPECS)


async def test_correct_empty_answer_is_not_a_failure() -> None:
    """Корректный пустой ответ закрывает ряд: спрашивать нечего.

    Это ровно то различие, которого не было: пустой ответ и сломанный контракт
    возвращали одинаковый `{}`.
    """
    client = _client([[] for _ in SPECS])
    repository = _repository()

    written = await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert written == 0
    repository.upsert_global_values.assert_not_awaited()


async def test_stop_keeps_priority_over_partial() -> None:
    """Остановка — отдельный исход и частичным неуспехом не подменяется."""
    client = _client([[_row(DAY)], SourceStoppedError(0)])
    repository = _repository()

    with pytest.raises(SourceStoppedError) as raised:
        await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert raised.value.rows_written == 1


# --- Ряды ЦБ -----------------------------------------------------------------


async def test_key_rate_survives_a_broken_curve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ставка сохранена, кривая остаётся работой, источник не закрыт."""
    monkeypatch.setattr(
        cbr, "fetch_key_rate", AsyncMock(return_value={DAY: Decimal("16.5")})
    )
    monkeypatch.setattr(
        cbr, "fetch_zcyc", AsyncMock(side_effect=ValueError("разметка страницы изменилась"))
    )
    repository = _repository()

    with pytest.raises(SourcePartialError) as raised:
        await ingest._sync_cbr_range(repository, DAY, DAY, client=None, should_stop=None)

    assert raised.value.rows_written == 1
    repository.upsert_global_values.assert_awaited_once_with(
        cbr.KEY_RATE_SERIES_ID, {DAY: Decimal("16.5")}
    )
    assert "кривая" in raised.value.detail


async def test_curve_survives_a_broken_key_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """И наоборот: падение ставки не отменяет обращение за кривой."""
    monkeypatch.setattr(cbr, "fetch_key_rate", AsyncMock(side_effect=ValueError("нет таблицы")))
    monkeypatch.setattr(
        cbr,
        "fetch_zcyc",
        AsyncMock(return_value={"CBR_ZCYC_yield_1y": {DAY: Decimal("15.1")}}),
    )
    repository = _repository()

    with pytest.raises(SourcePartialError) as raised:
        await ingest._sync_cbr_range(repository, DAY, DAY, client=None, should_stop=None)

    assert raised.value.rows_written == 1
    assert cbr.KEY_RATE_SERIES_ID in raised.value.unfinished


async def test_both_parts_collected_closes_cbr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cbr, "fetch_key_rate", AsyncMock(return_value={DAY: Decimal("16.5")}))
    monkeypatch.setattr(
        cbr,
        "fetch_zcyc",
        AsyncMock(return_value={"CBR_ZCYC_yield_1y": {DAY: Decimal("15.1")}}),
    )
    repository = _repository()

    written = await ingest._sync_cbr_range(repository, DAY, DAY, client=None, should_stop=None)

    assert written == 2
