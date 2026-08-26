# Financial AI

Платформа для AI-assisted управления инвестиционным портфелем и торговли на российском
фондовом рынке.

Система развивается поэтапно. Первая фича — **Investment Account State**: получение
актуального состояния инвестиционного счёта из T-Bank Invest API, сохранение его во
внутреннем хранилище и отображение в веб-интерфейсе.

---

## Статус

| Что | Состояние |
|---|---|
| Спецификация первой фичи | ✅ готова, уточнения закрыты |
| План реализации, контракты, модель данных | ✅ готовы |
| Дизайн интерфейса (Open Design) | ✅ утверждён и обновлён под уточнённые требования |
| Код | ⛔ ещё не реализован |

Репозиторий сейчас содержит только проектные артефакты. Разделы «Запуск» и «Разработка»
ниже описывают целевое состояние — команды заработают по мере реализации.

Текущая ветка разработки: `feature/investment-account-state`.

---

## Целевая архитектура

```text
рыночные и справочные данные
        ↓
     Daily ML  →  ранжирование активов  →  portfolio / target-position logic
                                                      ↓
                                          Intraday Execution ML
                                                      ↓
                                              исполнение сделок  →  брокер
                                                                      ↓
                                        Backend / PostgreSQL / Frontend
```

Наличие компонента на целевой диаграмме не означает, что он реализуется сейчас.
В объём первой фичи входят только выделенные ниже контейнеры.

### Контейнеры первой фичи

| Контейнер | Роль | Ходит в T-Bank |
|---|---|---|
| **frontend** | React-приложение; внутри образа nginx отдаёт статику и проксирует `/api` на backend-api | нет |
| **backend-api** | Публичный HTTP API: отдаёт сохранённое состояние счёта, статус синхронизации, настройку интервала; транслирует команду ручного обновления в worker | **нет** |
| **backend-worker** | Синхронизация с T-Bank Invest API по расписанию и по требованию, преобразование в доменную модель, запись в PostgreSQL | **да, единственный** |
| **postgres** | Последнее успешное состояние счёта, статус синхронизации, настройка интервала | нет |

Поток данных:

```text
T-Bank Invest API → backend-worker → PostgreSQL → backend-api → frontend
```

Ручное обновление:

```text
frontend → backend-api → (внутренний REST) backend-worker → T-Bank Invest API
```

Внутренний REST worker'а наружу не проксируется. Токен доступа к брокеру передаётся
**только** контейнеру `backend-worker`.

---

## Стек

### Backend

| Задача | Решение |
|---|---|
| Язык | Python 3.12 |
| HTTP API (публичный и внутренний) | FastAPI |
| Валидация, схемы, конфигурация | Pydantic v2, pydantic-settings |
| ORM / миграции | SQLAlchemy 2.0 (async), Alembic |
| Драйвер БД | asyncpg |
| Клиент T-Invest API | `t-tech-investments` (индекс T-Bank, см. ниже) |
| Фоновая синхронизация | asyncio-цикл с интервалом из БД |
| HTTP-клиент backend-api → worker | httpx |
| Менеджер пакетов | uv |

`backend-api` и `backend-worker` собираются из одного Python-пакета `backend/` с двумя
точками входа и разворачиваются как два контейнера.

### Frontend

| Задача | Решение |
|---|---|
| Язык и сборка | TypeScript, React 19, Vite, Node 22 LTS |
| Архитектура | Feature-Sliced Design |
| Server state, polling | TanStack Query |
| Таблица позиций и сортировка | TanStack Table |
| Менеджер пакетов | pnpm |
| Источник дизайна | Open Design, проект «Портфель FINANCIAL AI» |

### Инфраструктура

PostgreSQL 17, Docker Compose, nginx (внутри образа frontend).

---

## Структура репозитория

```text
backend/                      # Python-пакет: backend-api + backend-worker
├── Dockerfile
├── pyproject.toml
├── migrations/               # Alembic
├── src/financial_ai/
└── tests/

frontend/                     # React-приложение
├── Dockerfile                # multi-stage: node build → nginx:alpine
├── package.json
├── src/
└── tests/

deployments/docker-compose/   # всё, что относится к развёртыванию
├── docker-compose.yml
├── nginx/nginx.conf
├── .env.example
└── .env                      # реальные значения, не коммитится

specs/                        # спецификации фич (Spec Kit)
└── 001-investment-account-state/
    ├── spec.md               # требования
    ├── plan.md               # план реализации
    ├── research.md           # проверенные факты и решения
    ├── data-model.md         # схема хранения
    ├── contracts/            # контракты API
    ├── quickstart.md         # запуск и проверочные сценарии
    └── checklists/

.specify/memory/constitution.md   # обязательные инженерные правила проекта
AGENTS.md                         # контекст проекта для AI-агентов
```

Dockerfile каждого компонента лежит рядом с его кодом; compose, конфиг nginx и переменные
окружения — в `deployments/`.

---

## Конфигурация

Все секреты и зависящие от окружения значения передаются через переменные окружения.
Реальные значения — в `deployments/docker-compose/.env`, который **не коммитится**.
Шаблон без значений — `deployments/docker-compose/.env.example`.

| Переменная | Кому нужна | Назначение |
|---|---|---|
| `TBANK_INVEST_READ_TOKEN` | **только** backend-worker | Персональный токен Т-Банк Invest с правами **только на чтение** |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | postgres | Учётные данные БД |
| `DATABASE_URL` | backend-api, backend-worker | Подключение к PostgreSQL |
| `WORKER_INTERNAL_URL` | backend-api | Адрес внутреннего REST worker'а |

Правила:

- токен создаётся с правами только на чтение — система не инициирует торговых операций;
- токен не вводится в интерфейсе, не хранится в БД и не попадает в логи и ответы API;
- номер брокерского договора отображается только в маскированном виде.

---

## Запуск

### Docker Compose

```bash
cp deployments/docker-compose/.env.example deployments/docker-compose/.env
# заполнить TBANK_INVEST_READ_TOKEN и POSTGRES_PASSWORD

docker compose -f deployments/docker-compose/docker-compose.yml up --build
```

Интерфейс: <http://localhost:8080>

Проверка живости:

```bash
curl -s localhost:8080/api/health
```

### Локальная разработка

```bash
# PostgreSQL
docker compose -f deployments/docker-compose/docker-compose.yml up postgres

# backend
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn financial_ai.api.app:app --port 8001 --reload      # backend-api
uv run uvicorn financial_ai.worker.app:app --port 8000 --reload   # backend-worker

# frontend
cd frontend
pnpm install
pnpm dev            # проксирует /api на backend-api, nginx не нужен
```

### Установка SDK T-Invest

Пакет `t-tech-investments` **не устанавливается с публичного PyPI** — там он на карантине
и не содержит файлов. Нужен индекс T-Bank:

```bash
pip install t-tech-investments==1.49.3 \
  --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
```

Индекс зафиксирован в `backend/pyproject.toml`, поэтому `uv sync` делает это сам.

---

## Проверки качества

Фича не считается завершённой, пока все применимые проверки не проходят.

```bash
# backend
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest

# frontend
cd frontend
pnpm lint && pnpm prettier --check .
pnpm tsc --noEmit
pnpm test
```

---

## Процесс разработки

Проект ведётся по spec-driven подходу на базе [Spec Kit](https://github.com/github/spec-kit):
`specify` → `clarify` → `plan` → `tasks` → `implement`.

Обязательные инженерные правила — в [`.specify/memory/constitution.md`](.specify/memory/constitution.md).
Ключевое из них:

- существенная неоднозначность не разрешается предположением — она уточняется и фиксируется
  в спецификации;
- реализация не должна молча противоречить спецификации или утверждённому дизайну: сначала
  правится исходный артефакт;
- каждая фича разрабатывается в отдельной ветке `feature/<описание>` или `fix/<описание>`;
- секреты не попадают в исходный код, репозиторий и логи.

Контекст проекта для AI-агентов — в [`AGENTS.md`](AGENTS.md).

---

## Документы первой фичи

- [Спецификация](specs/001-investment-account-state/spec.md) — требования и сценарии
- [План реализации](specs/001-investment-account-state/plan.md) — архитектура и решения
- [Research](specs/001-investment-account-state/research.md) — проверенные факты, альтернативы
- [Модель данных](specs/001-investment-account-state/data-model.md)
- [Контракт Backend-API](specs/001-investment-account-state/contracts/backend-api.md)
- [Контракт Backend-Worker](specs/001-investment-account-state/contracts/worker-internal-api.md)
- [Quickstart](specs/001-investment-account-state/quickstart.md) — запуск и проверочные сценарии
