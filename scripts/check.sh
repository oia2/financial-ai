#!/usr/bin/env bash
#
# Полный набор проверок качества (Принцип IV Constitution).
#
# Единственная команда, которой проверяется готовность изменения. Она
# существует потому, что частичные прогоны уже дважды пропускали дефекты:
# ruff, запущенный по `src/` и `tests/`, не видел `migrations/`, а образы
# не собирались до самого конца работы, хотя тег базового образа был неверным.
#
# Использование:
#   scripts/check.sh              # всё, включая сборку образов
#   scripts/check.sh --no-docker  # быстрый цикл разработки; НЕ является полным гейтом
#
# Требуется доступная PostgreSQL. Адрес берётся из DATABASE_URL либо из
# deployments/docker-compose/.env.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

WITH_DOCKER=1
[[ "${1:-}" == "--no-docker" ]] && WITH_DOCKER=0

FAILED=()

step() {
    local name="$1"
    shift
    printf '\n=== %s ===\n' "$name"
    if "$@"; then
        printf '  OK: %s\n' "$name"
    else
        printf '  ПРОВАЛ: %s\n' "$name"
        FAILED+=("$name")
    fi
}

# Конфигурация БД: явная переменная важнее файла окружения.
if [[ -z "${DATABASE_URL:-}" ]]; then
    ENV_FILE="deployments/docker-compose/.env"
    PORT=5432
    if [[ -f "$ENV_FILE" ]]; then
        PORT="$(grep -E '^POSTGRES_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '\r' || echo 5432)"
        PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r"'"'" || true)"
    fi
    export DATABASE_URL="postgresql+asyncpg://financial_ai:${PASSWORD:-financial_ai_local}@localhost:${PORT:-5432}/financial_ai"
fi
printf 'БД для тестов: %s\n' "${DATABASE_URL//:*@/:***@}"

# ---------- backend ----------
# Точка запуска — корень пакета, а не отдельные каталоги: проверяется всё,
# включая migrations/ и конфигурационные файлы.
step "backend: ruff check"        bash -c "cd '$ROOT/backend' && uv run ruff check ."
step "backend: ruff format"       bash -c "cd '$ROOT/backend' && uv run ruff format --check ."
step "backend: mypy"              bash -c "cd '$ROOT/backend' && uv run mypy"
step "backend: pytest"            bash -c "cd '$ROOT/backend' && uv run pytest -q"

# ---------- миграции на чистой БД ----------
step "backend: alembic на чистой БД" bash -c "cd '$ROOT/backend' && \
    DATABASE_URL=\"\${DATABASE_URL%/*}/financial_ai_gatecheck\" uv run python -c \"
import asyncio, asyncpg, os, urllib.parse as u
dsn = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
parts = u.urlsplit(dsn)
async def main():
    c = await asyncpg.connect(u.urlunsplit(parts._replace(path='/postgres')))
    await c.execute('drop database if exists financial_ai_gatecheck')
    await c.execute('create database financial_ai_gatecheck')
    await c.close()
asyncio.run(main())
\" && DATABASE_URL=\"\${DATABASE_URL%/*}/financial_ai_gatecheck\" uv run alembic upgrade head >/dev/null"

# ---------- эмулятор Daily ML ----------
# Отдельный uv-проект: свои зависимости и своя конфигурация инструментов.
# Сборка образа отдельного шага не требует — docker compose build ниже собирает
# все сервисы файла, включая эмулятор.
step "daily-ml-emulator: ruff check"  bash -c "cd '$ROOT/daily-ml-emulator' && uv run ruff check ."
step "daily-ml-emulator: ruff format" bash -c "cd '$ROOT/daily-ml-emulator' && uv run ruff format --check ."
step "daily-ml-emulator: mypy"        bash -c "cd '$ROOT/daily-ml-emulator' && uv run mypy"
step "daily-ml-emulator: pytest"      bash -c "cd '$ROOT/daily-ml-emulator' && uv run pytest -q"

# ---------- frontend ----------
step "frontend: eslint"           bash -c "cd '$ROOT/frontend' && ./node_modules/.bin/eslint ."
step "frontend: prettier"         bash -c "cd '$ROOT/frontend' && ./node_modules/.bin/prettier --check ."
step "frontend: tsc"              bash -c "cd '$ROOT/frontend' && ./node_modules/.bin/tsc --noEmit"
step "frontend: vitest"           bash -c "cd '$ROOT/frontend' && ./node_modules/.bin/vitest run"

# Сборка отдельно от образа: она проверяет, что в dist попали не только модули,
# но и статические файлы из public/. Иконка вкладки уже терялась ровно так —
# Dockerfile копировал src/, но не public/, и сборка при этом проходила.
step "frontend: сборка и статика" bash -c "cd '$ROOT/frontend' && ./node_modules/.bin/vite build >/dev/null && test -f dist/favicon.svg && test -f dist/index.html && python -c \"
import io, xml.dom.minidom as m
m.parseString(io.open('dist/favicon.svg', encoding='utf-8').read())
\""

# ---------- сборка образов ----------
# Собирается всегда, кроме явного --no-docker: неверный тег базового образа
# ломает только сборку и никакими другими проверками не ловится.
if [[ "$WITH_DOCKER" == "1" ]]; then
    step "docker compose build" docker compose -f deployments/docker-compose/docker-compose.yml build
else
    printf '\n=== docker compose build пропущен (--no-docker) ===\n'
    printf '  Это НЕ полный гейт: перед сдачей запустите scripts/check.sh без флага.\n'
fi

# ---------- итог ----------
printf '\n========================================\n'
if [[ ${#FAILED[@]} -eq 0 ]]; then
    printf 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ\n'
    exit 0
fi

printf 'ПРОВАЛЕНО: %d\n' "${#FAILED[@]}"
printf '  - %s\n' "${FAILED[@]}"
exit 1
