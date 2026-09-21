"""Тесты форм адреса MOEX ISS.

Две формы существуют не для красоты: перенос оригинала «как есть» дал бы 288
обращений к бирже в день там, где достаточно одного.
"""

from __future__ import annotations

from financial_ai.market_data.iss import urls

BASE = "https://iss.moex.com/iss"


def test_daily_url_has_no_ticker_in_path() -> None:
    """Ежедневный добор: одна дата, все бумаги. Тикера в пути быть не должно."""
    url = urls.history_by_date_url(BASE, board="TQBR")
    assert url.endswith("/boards/TQBR/securities.json")
    assert "SBER" not in url


def test_daily_params_carry_the_date() -> None:
    params = urls.history_by_date_params(
        "2026-08-28", start=0, limit=100, columns=("SECID", "CLOSE")
    )
    assert params["date"] == "2026-08-28"
    assert params["iss.only"] == "history,history.cursor"
    assert params["history.columns"] == "SECID,CLOSE"


def test_backfill_url_has_ticker_in_path() -> None:
    """Первичная загрузка: одна бумага, весь диапазон. Здесь тикер в пути уместен."""
    url = urls.history_by_security_url(BASE, board="TQBR", secid="sber")
    assert url.endswith("/boards/TQBR/securities/SBER.json")


def test_backfill_url_without_board() -> None:
    url = urls.history_by_security_url(BASE, board=None, secid="SBER")
    assert "/boards/" not in url
    assert url.endswith("/securities/SBER.json")


def test_backfill_params_carry_the_range() -> None:
    params = urls.history_by_security_params(
        "1990-01-01", "2026-08-28", start=200, limit=100, columns=("TRADEDATE",)
    )
    assert params["from"] == "1990-01-01"
    assert params["till"] == "2026-08-28"
    assert params["start"] == 200
    assert params["iss.only"] == "history,history.cursor"


def test_two_forms_are_different() -> None:
    """Формы не должны совпасть после какой-нибудь «унификации»."""
    daily = urls.history_by_date_url(BASE, board="TQBR")
    backfill = urls.history_by_security_url(BASE, board="TQBR", secid="SBER")
    assert daily != backfill


# --- адрес срочного рынка (spec 005, FR-019) ---------------------------------


def test_futures_url_has_no_board_segment() -> None:
    """Оригинал строит адрес срочного рынка БЕЗ `boards/`.

    Прежняя версия вставляла доску всегда, поэтому в адрес попадала `TQBR` —
    доска акций, — и сочетания такого на бирже нет: Brent не собирался ни разу.
    """
    url = urls.history_by_date_url(
        "https://iss.moex.com/iss", None, engine="futures", market="forts"
    )
    assert url == "https://iss.moex.com/iss/history/engines/futures/markets/forts/securities.json"
    assert "boards" not in url


def test_equity_url_still_carries_the_board() -> None:
    """На пути акций доска осмысленна и остаётся."""
    url = urls.history_by_date_url("https://iss.moex.com/iss", "TQBR")
    assert url.endswith("/markets/shares/boards/TQBR/securities.json")


# --- доска применяется только в своём разделе (spec 005) ---------------------


def _client() -> object:
    from financial_ai.market_data.iss.client import IssClient, IssConfig

    return IssClient(IssConfig(base_url=BASE, board="TQBR"))


def test_equity_section_keeps_the_configured_board() -> None:
    """На пути акций доска из конфигурации осмысленна."""
    assert _client()._board_for(None, None, None) == "TQBR"  # type: ignore[attr-defined]


def test_index_section_gets_no_board() -> None:
    """Индексы: `boards/TQBR` даёт пустой ответ, а не ошибку.

    Именно так четыре индекса собрались по одной сессии из 314 — дефект
    выглядел как отсутствие данных.
    """
    assert _client()._board_for(None, "stock", "index") is None  # type: ignore[attr-defined]


def test_futures_section_gets_no_board() -> None:
    """То же и на срочном рынке: так пропадал Brent."""
    assert _client()._board_for(None, "futures", "forts") is None  # type: ignore[attr-defined]


def test_explicit_board_is_kept_as_given() -> None:
    """Валютный ряд живёт на своей доске, и она задана явно."""
    assert _client()._board_for("CETS", "currency", "selt") == "CETS"  # type: ignore[attr-defined]
