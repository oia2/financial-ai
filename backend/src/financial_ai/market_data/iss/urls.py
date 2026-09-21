"""Построение адресов MOEX ISS.

Две формы, и разница между ними не косметическая.

Оригинал в исследовательском репозитории строит адрес только по тикеру —
`securities/{secid}.json` с диапазоном дат. Для первичной загрузки это верно:
одна бумага за всю историю. Для ежедневного добора нужна обратная форма — одна
дата за все бумаги, иначе получается 288 обращений к бирже ради 288 строк.
"""

from __future__ import annotations


def history_by_date_url(
    base_url: str, board: str | None, engine: str = "stock", market: str = "shares"
) -> str:
    """Адрес для ежедневного добора: одна дата, все бумаги раздела.

    Дата передаётся параметром запроса, а не путём: см. :func:`history_by_date_params`.

    **Доска необязательна, и это не мелочь.** Оригинал для срочного рынка
    строит адрес БЕЗ сегмента `boards/`
    (`br_continuous_history_sync/pipeline.py:148`). Прежняя версия вставляла
    доску всегда, поэтому в адрес срочного рынка подставлялась `TQBR` — доска
    акций, — и получалось сочетание, которого на бирже не существует: Brent не
    собирался ни разу, а в лог на каждой сессии шло «подходящего контракта не
    нашлось».
    """
    prefix = (
        f"{base_url.rstrip('/')}/history/engines/{engine.strip('/')}/markets/{market.strip('/')}"
    )
    if board:
        return f"{prefix}/boards/{board.strip('/').upper()}/securities.json"
    return f"{prefix}/securities.json"


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
        "iss.only": "history,history.cursor",
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
        "iss.only": "history,history.cursor",
        "iss.meta": "off",
        "history.columns": ",".join(columns),
    }


def futures_series_url(base_url: str) -> str:
    """Адрес списка серий срочного рынка.

    Отсюда берётся соответствие «базовый актив → код контракта»: правилом код
    не выводится (`SBER` → `SBRF`, `NVTK` → `NOTKM`), а файла соответствий
    оригинала в репозитории нет.
    """
    return f"{base_url.rstrip('/')}/statistics/engines/futures/markets/forts/series.json"


def futures_securities_url(base_url: str) -> str:
    """Адрес списка контрактов срочного рынка.

    Нужен ради открытого интереса: он разрешает выбор, когда у одной акции
    несколько кодов контракта.
    """
    return f"{base_url.rstrip('/')}/engines/futures/markets/forts/securities.json"


def equity_securities_url(base_url: str, board: str) -> str:
    """Адрес текущего состояния бумаг доски акций.

    Отсюда берётся размер лота: у него нет истории, это свойство инструмента на
    сегодня. Доска подставляется в адрес **своего** раздела — акций, а не
    какого-нибудь другого: подстановка доски в чужой раздел уже однажды стоила
    четырёх индексов, собранных по одной сессии из 314 (FR-019a фичи 005).
    """
    return (
        f"{base_url.rstrip('/')}/engines/stock/markets/shares"
        f"/boards/{board.strip('/').upper()}/securities.json"
    )


def index_analytics_url(base_url: str, index_id: str) -> str:
    """Адрес состава индекса с весами бумаг.

    **Это раздел аналитики, а не истории торгов.** Вес бумаги в индексе в истории
    торгов не публикуется вовсе: биржа отвечает `200`, строки приходят, колонки
    веса в них нет. Так в хранилище и накопились 62 584 строки без единого
    значения. Оригинал берёт веса отсюда
    (`iss_index_constituents_daily_sync/pipeline.py:150`).
    """
    return (
        f"{base_url.rstrip('/')}/statistics/engines/stock/markets/index"
        f"/analytics/{index_id.strip('/').upper()}.json"
    )


def index_titles_url(base_url: str) -> str:
    """Адрес перечня индексов с их краткими именами.

    Нужен секторам: название сектора — это имя отраслевого индекса.
    """
    return f"{base_url.rstrip('/')}/statistics/engines/stock/markets/index/analytics.json"


def security_description_url(base_url: str, secid: str) -> str:
    """Адрес описания инструмента.

    Общий раздел, не привязанный к рынку: один и тот же адрес отвечает и по
    акции, и по фьючерсной серии. Отсюда берётся идентификатор эмитента —
    единственное поле, которым связь акции и контракта подтверждается
    независимо от совпадения названий (сверено 2026-09-17, см. PROVENANCE.md).
    """
    return f"{base_url.rstrip('/')}/securities/{secid.strip().upper()}.json"
