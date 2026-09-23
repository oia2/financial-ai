"""Параллельный запуск backend-тестов из единого гейта.

Набор — около тысячи тестов, почти все с настоящей PostgreSQL; последовательно
это ~4 минуты, и почти всё время процессор простаивает в ожидании базы. Файлы
раскладываются по шардам с равным числом тестов, у каждого шарда своя тестовая
база (``<имя>_test_<n>``, её создаёт ``tests/conftest.py``), поэтому очистка
таблиц одного шарда не задевает другой.

Проверок меньше не становится: запускается тот же набор тех же тестов.
``pytest-xdist`` не используется потому, что добавить зависимость в
``uv.lock`` сейчас нельзя — индекс T-Bank недоступен для разрешения.

Запуск из каталога ``backend``: ``uv run python ../scripts/pytest_shards.py``.
"""

from __future__ import annotations

import concurrent.futures
import io
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

DEFAULT_DSN = "postgresql+asyncpg://financial_ai:financial_ai_local@localhost:5432/financial_ai"


def _shard_count() -> int:
    explicit = os.environ.get("PYTEST_SHARDS")
    if explicit:
        return max(1, int(explicit))
    return max(1, min(6, (os.cpu_count() or 2) // 2))


def _collect() -> dict[str, int]:
    """Число тестов в каждом файле — по сбору самого pytest, а не по тексту."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        sys.stdout.write(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "::" in line:
            path = line.split("::", 1)[0]
            counts[path] = counts.get(path, 0) + 1
    return counts


def _balance(counts: dict[str, int], shards: int) -> list[list[str]]:
    """Самые большие файлы — первыми, каждый в наименее загруженный шард."""
    buckets: list[tuple[int, list[str]]] = [(0, []) for _ in range(shards)]
    for path, count in sorted(counts.items(), key=lambda item: -item[1]):
        index = min(range(shards), key=lambda i: buckets[i][0])
        load, files = buckets[index]
        buckets[index] = (load + count, [*files, path])
    return [files for _, files in buckets if files]


def _database_for(shard: int) -> str:
    base = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    parts = urlsplit(base)
    name = parts.path.lstrip("/") or "financial_ai"
    return urlunsplit(parts._replace(path=f"/{name}_test_{shard}"))


def _run(shard: int, files: list[str]) -> tuple[int, int, str]:
    env = {**os.environ, "TEST_DATABASE_URL": _database_for(shard)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return shard, result.returncode, result.stdout + result.stderr


_SUMMARY = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")


def main() -> int:
    # Консоль Windows по умолчанию cp1251: вывод упавшего теста с символом вне
    # кодировки ронял сам запускатель, а не сообщал о провале.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    started = time.monotonic()
    shards = _balance(_collect(), _shard_count())
    totals: dict[str, int] = {}
    failed = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(shards)) as pool:
        futures = [pool.submit(_run, index, files) for index, files in enumerate(shards)]
        for future in concurrent.futures.as_completed(futures):
            shard, code, output = future.result()
            lines = output.strip().splitlines()
            summary = lines[-1] if lines else ""
            for number, kind in _SUMMARY.findall(summary):
                key = "error" if kind.startswith("error") else kind
                totals[key] = totals.get(key, 0) + int(number)
            if code != 0:
                failed = True
                sys.stdout.write(f"\n--- шард {shard}: код {code} ---\n{output}\n")

    elapsed = time.monotonic() - started
    parts = ", ".join(f"{count} {kind}" for kind, count in sorted(totals.items()))
    print(f"{parts} в {len(shards)} шардах за {elapsed:.1f} с")
    # Пропуск тестов с БД означает, что база недоступна, — в гейте это провал,
    # а не зелёный результат по одним unit-тестам.
    if totals.get("skipped") and os.environ.get("PYTEST_SHARDS_ALLOW_SKIPS") != "1":
        print("есть пропущенные тесты: проверьте доступность PostgreSQL")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
