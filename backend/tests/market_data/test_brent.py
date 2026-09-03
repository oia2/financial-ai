"""Тесты выбора фронтального контракта Brent.

Склейка здесь проще, чем в исследовательском репозитории: фронтальным считается
контракт с ближайшим неистёкшим сроком, а не выбранный по оценённому правилу
переката. Тесты фиксируют именно это правило — чтобы отличие было проверяемым,
а не подразумеваемым.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from financial_ai.market_data.sources.brent import select_front_contract

SESSION = dt.date(2026, 8, 28)


def _row(shortname: str, close: str = "72.5", secid: str | None = None) -> dict[str, object]:
    return {
        "SECID": secid or shortname.replace("-", "").replace(".", ""),
        "SHORTNAME": shortname,
        "TRADEDATE": SESSION.isoformat(),
        "CLOSE": close,
    }


def test_nearest_unexpired_contract_wins() -> None:
    rows = [_row("BR-12.26", "74.0"), _row("BR-9.26", "72.5"), _row("BR-10.26", "73.0")]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.expiry == dt.date(2026, 9, 30)
    assert contract.close == Decimal("72.5")


def test_expired_contracts_are_ignored() -> None:
    """Иначе ряд «залипал» бы на истёкшем контракте и перестал отражать рынок."""
    rows = [_row("BR-7.26", "70.0"), _row("BR-9.26", "72.5")]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.expiry == dt.date(2026, 9, 30)


def test_contract_of_current_month_is_still_front() -> None:
    """Контракт текущего месяца не считается истёкшим до конца месяца.

    С первым числом месяца в роли срока ряд перескакивал бы на следующий
    контракт в начале каждого месяца — когда текущий ещё торгуется.
    """
    rows = [_row("BR-8.26", "71.0"), _row("BR-9.26", "72.5")]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.expiry == dt.date(2026, 8, 31)
    assert contract.close == Decimal("71.0")


def test_no_suitable_contract_returns_none() -> None:
    assert select_front_contract([_row("BR-1.26")], SESSION) is None


def test_foreign_instruments_are_skipped() -> None:
    """В разделе FORTS торгуется не только нефть."""
    rows = [
        {"SECID": "SiZ6", "SHORTNAME": "Si-12.26", "CLOSE": "95000"},
        {"SECID": "RIZ6", "SHORTNAME": "RTS-12.26", "CLOSE": "110000"},
        _row("BR-9.26", "72.5"),
    ]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.close == Decimal("72.5")


def test_year_crossing_is_handled() -> None:
    """Порядок идёт по дате, а не по номеру месяца: декабрь 2026 раньше января 2027."""
    rows = [_row("BR-1.27", "75.0"), _row("BR-12.26", "74.0")]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.expiry == dt.date(2026, 12, 31)


def test_malformed_shortname_is_skipped() -> None:
    rows = [{"SECID": "BRX", "SHORTNAME": "BR-мусор", "CLOSE": "1"}, _row("BR-9.26", "72.5")]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.close == Decimal("72.5")


def test_missing_close_stays_none_not_zero() -> None:
    rows = [_row("BR-9.26", close="")]
    contract = select_front_contract(rows, SESSION)
    assert contract is not None
    assert contract.close is None


def test_tie_is_broken_deterministically() -> None:
    """При равных сроках порядок не должен зависеть от порядка обхода ответа."""
    rows = [_row("BR-9.26", "1", secid="BRU6_B"), _row("BR-9.26", "2", secid="BRU6_A")]
    first = select_front_contract(rows, SESSION)
    second = select_front_contract(list(reversed(rows)), SESSION)
    assert first is not None and second is not None
    assert first.secid == second.secid
