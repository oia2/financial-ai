"""Явный результат проверки применимой работы источника."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from financial_ai.market_data.plan import FAILURE_UNPUBLISHED

RESULT_VALUE = "value"
RESULT_CONFIRMED_ABSENCE = "confirmed_absence"
RESULT_NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class WorkEvidence:
    """Одна проверенная единица работы источника за торговую сессию."""

    session_date: dt.date
    work_key: str
    result_kind: str = RESULT_VALUE
    reason_code: str = "verified_response"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Данные, доказательства и итог проверки полного контракта источника."""

    rows_written: int
    evidence: tuple[WorkEvidence, ...]
    complete: bool = True
    detail: str | None = None
    counts_as_unavailable: bool = False
    # Причина незавершённости (FR-033f). Незавершённая проверка по умолчанию —
    # источник не отдал нужное: не ответил за дату или ответил не всё.
    failure_kind: str = "source"


def one_session(
    rows_written: int,
    session_date: dt.date,
    work_key: str,
    *,
    has_value: bool,
    reason_code: str = "verified_response",
) -> VerificationResult:
    """Результат полного ответа по одной дате и одной единице работы."""
    return VerificationResult(
        rows_written=rows_written,
        evidence=(
            WorkEvidence(
                session_date=session_date,
                work_key=work_key,
                result_kind=RESULT_VALUE if has_value else RESULT_CONFIRMED_ABSENCE,
                reason_code=reason_code if has_value else "verified_empty_response",
            ),
        ),
    )


def awaiting_publication(detail: str) -> VerificationResult:
    """Источник за дату ещё ничего не опубликовал (FR-032h, FR-032i).

    Не отсутствие и не отказ: доказательства нет, попытки сессии не
    расходуются, повтор — с обычной выдержкой. Для значения, которое существует
    в каждую торговую сессию, пустой ответ означает только это.
    """
    return VerificationResult(
        rows_written=0,
        evidence=(),
        complete=False,
        detail=detail,
        failure_kind=FAILURE_UNPUBLISHED,
    )


def not_applicable_value(detail: str) -> VerificationResult:
    """Ответ со строками, но без применимого значения: вопрос к источнику."""
    return VerificationResult(rows_written=0, evidence=(), complete=False, detail=detail)


def required_work_keys(source_id: str) -> frozenset[str]:
    """Минимальные независимые единицы, которые источник обязан доказать."""
    return _REQUIRED_WORK_KEYS.get(source_id, frozenset({"source_complete"}))


_REQUIRED_WORK_KEYS: dict[str, frozenset[str]] = {
    "equity_d1": frozenset({"board:TQBR"}),
    "equity_agg": frozenset({"board:TQBR"}),
    "global_series": frozenset({"IMOEX", "RTSI", "RGBI", "RVI", "USD_ISS"}),
    "index_constituents": frozenset({"index:IMOEX"}),
    "brent": frozenset({"BRENT_FRONT"}),
    "cbr": frozenset({"CBR_KEY_RATE", "CBR_ZCYC_CURVE"}),
    "futures_positions": frozenset({"applicable_links"}),
}
