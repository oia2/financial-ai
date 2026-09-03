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
    assert params["iss.only"] == "history"
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


def test_two_forms_are_different() -> None:
    """Формы не должны совпасть после какой-нибудь «унификации»."""
    daily = urls.history_by_date_url(BASE, board="TQBR")
    backfill = urls.history_by_security_url(BASE, board="TQBR", secid="SBER")
    assert daily != backfill
