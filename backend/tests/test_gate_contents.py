"""Живые сверки не входят в автоматический гейт (FR-026, SC-012).

Требование, которое иначе соблюдалось бы только по памяти. Команды `verify-*`
обращаются к настоящим источникам; в среде сборки сети нет, и гейт стал бы
недетерминированным — падал бы от чужой недоступности, а не от наших дефектов.

Проверяется текстом скрипта намеренно: гейт — это и есть его текст.
"""

from __future__ import annotations

from pathlib import Path

CHECK_SH = Path(__file__).resolve().parents[2] / "scripts" / "check.sh"

LIVE_COMMANDS = ("verify-cbr", "verify-brent", "verify-positions", "verify-reference")


def test_gate_script_exists() -> None:
    assert CHECK_SH.is_file()


def test_gate_does_not_run_live_checks() -> None:
    """Ни одна сверка с живым источником в гейт не попадает."""
    script = CHECK_SH.read_text(encoding="utf-8")

    for command in LIVE_COMMANDS:
        assert command not in script


def test_gate_still_runs_the_whole_test_suite() -> None:
    """Отсутствие живых сверок не должно превратиться в отсутствие тестов."""
    script = CHECK_SH.read_text(encoding="utf-8")

    assert "uv run pytest" in script
    assert "uv run ruff check ." in script
    assert "uv run mypy" in script
