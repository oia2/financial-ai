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

from financial_ai.market_data import ingest, plan
from financial_ai.market_data.interrupt import SourceStoppedError
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
    # Доказанного раньше нет: источник спрашивает весь остаток (FR-033d).
    repository.work_evidence_for_sessions = AsyncMock(return_value=[])
    return repository


def _row(day: dt.date) -> dict[str, object]:
    return {"SECID": "IMOEX", "TRADEDATE": day.isoformat(), "CLOSE": Decimal("3200.55")}


# --- Глобальные ряды ---------------------------------------------------------


async def test_one_series_of_five_saves_value_but_does_not_close_source() -> None:
    """1 успешный ряд + 4 ошибки: ряд сохранён, источник незавершён."""
    client = _client(
        [
            [_row(DAY)],
            IssError("нет ответа"),
            IssError("нет ответа"),
            IssError("нет ответа"),
            IssError("нет ответа"),
        ]
    )
    repository = _repository()

    result = await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    # Полученное сохранено: остановка записи не отменяет.
    assert result.rows_written == 1
    assert result.complete is False
    repository.upsert_global_values.assert_awaited_once()
    # И названо, что именно осталось работой, — а не «данных нет».
    assert "RTSI" in (result.detail or "")


async def test_all_series_failing_is_a_failure_with_nothing_written() -> None:
    """Все ряды упали: сохранять нечего, успехом это не становится."""
    client = _client(IssError("нет ответа"))
    repository = _repository()

    result = await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert result.rows_written == 0
    assert result.complete is False
    repository.upsert_global_values.assert_not_awaited()


async def test_all_series_succeeding_closes_the_source() -> None:
    """Вся применимая работа обработана — источник закрыт, исключения нет."""
    client = _client([[_row(DAY)] for _ in SPECS])
    repository = _repository()

    result = await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert result.rows_written == len(SPECS)
    assert result.complete is True


async def test_correct_empty_answer_is_awaiting_publication() -> None:
    """Корректный пустой ответ — ожидание публикации, а не отсутствие (FR-032i).

    Ряды существуют в каждую торговую сессию. Прежде пустой ответ закрывал
    ряд подтверждённым отсутствием, и значение дня терялось навсегда.
    Сломанный контракт при этом остаётся отказом источника.
    """
    client = _client([[] for _ in SPECS])
    repository = _repository()

    result = await global_series.sync_iss_series_range(client, repository, DAY, DAY, SPECS)

    assert result.rows_written == 0
    assert result.complete is False
    assert result.evidence == ()
    assert result.failure_kind == plan.FAILURE_UNPUBLISHED
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
    monkeypatch.setattr(cbr, "fetch_key_rate", AsyncMock(return_value={DAY: Decimal("16.5")}))
    monkeypatch.setattr(
        cbr, "fetch_zcyc", AsyncMock(side_effect=ValueError("разметка страницы изменилась"))
    )
    repository = _repository()

    result = await ingest._sync_cbr_range(repository, DAY, DAY, client=None, should_stop=None)

    assert result.rows_written == 1
    assert result.complete is False
    repository.upsert_global_values.assert_awaited_once_with(
        cbr.KEY_RATE_SERIES_ID, {DAY: Decimal("16.5")}
    )
    assert "кривая" in (result.detail or "")


async def test_curve_survives_a_broken_key_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """И наоборот: падение ставки не отменяет обращение за кривой."""
    monkeypatch.setattr(cbr, "fetch_key_rate", AsyncMock(side_effect=ValueError("нет таблицы")))
    monkeypatch.setattr(
        cbr,
        "fetch_zcyc",
        AsyncMock(return_value={"CBR_ZCYC_yield_1y": {DAY: Decimal("15.1")}}),
    )
    repository = _repository()

    result = await ingest._sync_cbr_range(repository, DAY, DAY, client=None, should_stop=None)

    assert result.rows_written == 1
    assert result.complete is False
    assert cbr.KEY_RATE_SERIES_ID in (result.detail or "")


async def test_both_parts_collected_closes_cbr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cbr, "fetch_key_rate", AsyncMock(return_value={DAY: Decimal("16.5")}))
    monkeypatch.setattr(
        cbr,
        "fetch_zcyc",
        AsyncMock(
            return_value={
                f"CBR_ZCYC_{term}": {DAY: Decimal("15.1")} for term in cbr.REQUIRED_ZCYC_TERMS
            }
        ),
    )
    repository = _repository()

    result = await ingest._sync_cbr_range(repository, DAY, DAY, client=None, should_stop=None)

    assert result.rows_written == 1 + len(cbr.REQUIRED_ZCYC_TERMS)
    assert result.complete is True


async def test_missing_date_inside_range_remains_unproved() -> None:
    next_day = DAY + dt.timedelta(days=3)
    client = _client([[_row(DAY)] for _ in SPECS])
    repository = _repository()

    result = await global_series.sync_iss_series_range(
        client,
        repository,
        DAY,
        next_day,
        SPECS,
        required_dates=(DAY, next_day),
    )

    assert result.complete is False
    assert next_day.isoformat() in (result.detail or "")
    assert {item.session_date for item in result.evidence} == {DAY}


async def test_fetch_failure_stays_a_source_failure() -> None:
    """Несостоявшееся обращение — отказ источника, а не ожидание публикации."""
    client = _client([[_row(DAY)], IssError("нет ответа"), [_row(DAY)], [_row(DAY)], [_row(DAY)]])

    result = await global_series.sync_iss_series_range(client, _repository(), DAY, DAY, SPECS)

    assert result.complete is False
    assert result.failure_kind == plan.FAILURE_SOURCE


async def test_empty_cbr_tables_are_awaiting_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ставка действует каждый день, кривую за день выкладывают позже (FR-032i).

    Прежде пустая таблица ставки записывалась подтверждённым отсутствием, а
    невыложенная кривая расходовала попытки сессии как отказ источника.
    """
    monkeypatch.setattr(cbr, "fetch_key_rate", AsyncMock(return_value={}))
    monkeypatch.setattr(cbr, "fetch_zcyc", AsyncMock(return_value={}))

    result = await ingest._sync_cbr_range(
        _repository(), DAY, DAY, client=None, should_stop=None, required_dates=(DAY,)
    )

    assert result.complete is False
    assert result.evidence == ()
    assert result.failure_kind == plan.FAILURE_UNPUBLISHED


@pytest.mark.parametrize("source", ["quotes", "aggregates"])
async def test_empty_tqbr_board_is_awaiting_publication(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Доска TQBR в торговую сессию не бывает пустой (FR-032i)."""
    from financial_ai.market_data.sources import equity_agg, equity_d1

    module = equity_d1 if source == "quotes" else equity_agg
    monkeypatch.setattr(module, "fetch_equity_board_rows", AsyncMock(return_value=[]))
    repository = _repository()
    repository.aliases_on = AsyncMock(return_value={})

    sync = equity_d1.sync_equity_daily if source == "quotes" else equity_agg.sync_equity_aggregates
    result = await sync(Mock(), repository, DAY)

    assert result.complete is False
    assert result.evidence == ()
    assert result.failure_kind == plan.FAILURE_UNPUBLISHED
