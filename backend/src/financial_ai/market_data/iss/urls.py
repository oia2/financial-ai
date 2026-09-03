"""Построение адресов MOEX ISS.

Две формы, и разница между ними не косметическая.

Оригинал в исследовательском репозитории строит адрес только по тикеру —
`securities/{secid}.json` с диапазоном дат. Для первичной загрузки это верно:
одна бумага за всю историю. Для ежедневного добора нужна обратная форма — одна
дата за все бумаги, иначе получается 288 обращений к бирже ради 288 строк.
"""

from __future__ import annotations


def history_by_date_url(
    base_url: str, board: str, engine: str = "stock", market: str = "shares"
) -> str:
    """Адрес для ежедневного добора: одна дата, все бумаги доски.

    Дата передаётся параметром запроса, а не путём: см. :func:`history_by_date_params`.
    """
    return (
        f"{base_url.rstrip('/')}/history/engines/{engine.strip('/')}"
        f"/markets/{market.strip('/')}/boards/{board.strip('/').upper()}/securities.json"
    )


def history_by_security_url(
    base_url: str,
    board: str | None,
    secid: str,
    engine: str = "stock",
    market: str = "shares",
) -> str:
    """Адрес для первичной загрузки: одна бумага, диапазон дат.

    Совпадает с формой оригинала (`_build_history_url`).
    """
    prefix = (
        f"{base_url.rstrip('/')}/history/engines/{engine.strip('/')}/markets/{market.strip('/')}"
    )
    security = secid.strip().upper()
    if board:
        return f"{prefix}/boards/{board.strip('/').upper()}/securities/{security}.json"
    return f"{prefix}/securities/{security}.json"


def history_by_date_params(
    session_date: str, start: int, limit: int, columns: tuple[str, ...]
) -> dict[str, str | int]:
    """Параметры запроса за одну дату."""
    return {
        "date": session_date,
        "start": start,
        "limit": limit,
        "iss.only": "history",
        "iss.meta": "off",
        "history.columns": ",".join(columns),
    }


def history_by_security_params(
    date_from: str, date_till: str, start: int, limit: int, columns: tuple[str, ...]
) -> dict[str, str | int]:
    """Параметры запроса по одной бумаге за диапазон дат."""
    return {
        "from": date_from,
        "till": date_till,
        "start": start,
        "limit": limit,
        "iss.only": "history",
        "iss.meta": "off",
        "history.columns": ",".join(columns),
    }
