# Implementation Plan: Investment Account State

**Branch**: `feature/investment-account-state` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-investment-account-state/spec.md`

**Основание**: черновик плана владельца проекта (архитектурный поток, роли контейнеров,
предложенный стек) + результаты проверки в [research.md](./research.md).

## Summary

Первый сквозной сценарий Financial AI: `Backend-Worker` читает состояние брокерского счёта
из T-Invest API рекомендованным SDK, приводит его к внутренней доменной модели и атомарно
сохраняет в PostgreSQL; `Backend-API` отдаёт сохранённое состояние вместе со статусом
синхронизации; `Frontend` отображает портфель по утверждённому дизайну Open Design и
различает три причины несвежести данных — устаревание, сбой T-Bank API и обрыв связи с
самим сервером Financial AI.

Ключевые технические решения: доступ к брокеру только read-only токеном из env
у одного контейнера; фоновая синхронизация — asyncio-цикл с интервалом из БД (не
APScheduler); дедупликация ручной и фоновой синхронизации через lock; вся денежная
арифметика на `Decimal`/`NUMERIC(28,9)`; частота опроса фронтом развязана с частотой
обращения к брокеру.

## Technical Context

**Language/Version**: Python 3.12 (backend), TypeScript 5.x / Node 22 LTS (frontend)

**Primary Dependencies**:
FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0 (async), asyncpg, Alembic, httpx,
`t-tech-investments==1.49.3` (индекс `opensource.tbank.ru`, **не публичный PyPI** — см.
research §1); React 19, Vite, TanStack Query, TanStack Table, zod

**Storage**: PostgreSQL 17; денежные и количественные величины — `NUMERIC(28, 9)`, время — `TIMESTAMPTZ` (UTC)

**Testing**: pytest + pytest-asyncio + respx (backend), vitest + Testing Library + msw (frontend), Alembic-миграции на чистой БД в CI

**Target Platform**: Linux-контейнеры, docker compose; браузеры — актуальные desktop и mobile

**Project Type**: Web application (frontend + два backend-сервиса + PostgreSQL за Nginx)

**Performance Goals**: SC-001 — отрисовка состояния или состояния загрузки < 2 с в 95% открытий; SC-004 — ручное обновление ≤ 5 с в 95% случаев; SC-010 — отображаемое состояние отстаёт от сохранённого не более чем на 10% интервала

**Constraints**: обращения к T-Bank API строго read-only (FR-005); токен только в
`Backend-Worker` и только из окружения (FR-021, FR-023); сохранённое состояние не
затирается частичными данными (FR-008); отсутствие связи с сервером и сбой брокера —
различимые состояния (FR-037)

**Scale/Scope**: один пользователь, один брокерский счёт (FR-025), один экран «Портфель»,
портфель в пределах сотен позиций

---

## Constitution Check

*GATE: пройден до Phase 0, перепроверен после Phase 1 и после поправки конституции 1.1.0.*

| Принцип | Статус | Как обеспечивается |
|---|---|---|
| I. Неоднозначность не разрешается предположением | ✅ | Все технические развилки были вынесены на согласование; решения приняты владельцем проекта 2026-08-26 и зафиксированы в [§9](#9-согласованные-решения). Открытыми остаются два вопроса размещения конфигурации, не влияющие на состав задач |
| II. Простота, качество и расширяемость | ✅ | Отказ от APScheduler, от очереди задач, от TanStack Router/Form, от истории снимков, от разделения backend на два пакета — каждый отказ обоснован в research.md |
| III. Согласованность спецификации и реализации | ✅ | Расхождения с дизайном устранены: 2026-08-26 в Open Design добавлены настройка интервала и состояние «нет связи с сервером», удалены элементы подключения брокера, состояние «Брокер не подключён» переосмыслено как «Доступ не сконфигурирован». Задание — [design-update-prompt.md](./design-update-prompt.md), проверка — research §10 |
| IV. Обязательные проверки качества | ✅ | ruff, mypy, pytest / eslint, tsc, vitest / alembic upgrade — research §12. В исходном черновике отсутствовали |
| V. Чистота и структура репозитория | ✅ | Структура ниже; deployment-артефакты в `deployments/docker-compose/` |
| VI. Изоляция разработки по Git-веткам | ✅ | `feature/investment-account-state`, запушена в origin |
| VII. Безопасность и конфигурация | ✅ | Токен — только env, только контейнер Worker; `.env.example` без значений; фильтр секретов в логах; маскированный номер договора в API |
| VIII. Дизайн-инструмент как источник истины | ✅ | Добавлен конституцией 1.1.0. Изначально был нарушен: вёрстка написана «по мотивам» артефакта, а ревью сравнивало только тексты состояний. Исправлено 2026-08-26 — CSS артефакта перенесён в `frontend/src/app/styles/design.css`, разметка компонентов повторяет его структуру и имена классов, соответствие проверяется сопоставлением множеств классов |
| IX. Актуальность проектной документации | ✅ | Добавлен конституцией 1.1.0. `README.md` отражает фактическое состояние кода, стек и блокирующий токен; `AGENTS.md` больше не ссылается на отсутствующую диаграмму; команды проверок в README и quickstart заменены единым `scripts/check.sh` |

**Вывод гейта**: пройден по всем девяти принципам конституции 1.1.0. Открытых решений, влияющих на состав
задач, не осталось — `/speckit-tasks` может быть запущен.

---

## Project Structure

### Documentation (this feature)

```text
specs/001-investment-account-state/
├── spec.md
├── plan.md              # этот файл
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1
│   ├── backend-api.md
│   └── worker-internal-api.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks — НЕ создаётся этой командой
```

### Source Code (repository root)

```text
backend/                              # один Python-пакет, два контейнера (research §8)
├── Dockerfile                        # один образ, из него запускаются два контейнера:
│                                     # backend-api и backend-worker (разные команды)
├── pyproject.toml                    # зависимости + extra-index-url T-Bank
├── uv.lock
├── alembic.ini
├── migrations/versions/
├── src/financial_ai/
│   ├── config.py                     # pydantic-settings; TBANK_INVEST_READ_TOKEN только здесь
│   ├── logging.py                    # JSON-логи + фильтр секретов (FR-030, SC-009)
│   ├── db/
│   │   ├── engine.py
│   │   └── models.py                 # investment_account, portfolio_position,
│   │                                 # account_state, broker_sync_state,
│   │                                 # account_refresh_settings
│   ├── domain/
│   │   ├── models.py                 # доменные модели на Decimal
│   │   └── portfolio.py              # доли, P&L, суммы (FR-011, SC-003)
│   ├── broker/
│   │   ├── protocol.py               # интерфейс адаптера (граница для тестов)
│   │   ├── tinvest.py                # t_tech.invest → domain
│   │   └── errors.py                 # классификация: unavailable / rejected / rate_limited
│   ├── sync/
│   │   ├── service.py                # sync_account_state() — общая для авто и ручной
│   │   ├── lock.py                   # asyncio.Lock + pg advisory lock (FR-029, FR-033)
│   │   └── scheduler.py              # asyncio-цикл, интервал из БД (research §2, §3)
│   ├── api/                          # Backend-API (публичный)
│   │   ├── app.py
│   │   ├── routes/{portfolio,settings,health}.py
│   │   └── schemas.py                # Pydantic-схемы ответа, Decimal → str
│   └── worker/                       # Backend-Worker (внутренний)
│       ├── app.py                    # FastAPI + lifespan со scheduler
│       └── routes/{sync,health}.py
└── tests/{unit,integration,contract}/

frontend/
├── Dockerfile                        # multi-stage: node build → nginx:alpine (runtime)
├── package.json, vite.config.ts, tsconfig.json
├── public/favicon.svg                 # иконка вкладки
├── src/                              # Feature-Sliced Design
│   ├── app/                          # провайдеры, QueryClient, styles/design.css из артефакта
│   ├── pages/portfolio/
│   ├── widgets/{app-header,capital-strip,positions-section,sync-status-banner}/
│   ├── features/{refresh-now,refresh-interval-setting}/
│   ├── entities/portfolio/           # типы, queries, селекторы
│   └── shared/{api,ui,lib}/          # lib: decimal, format, plural, preferences
└── tests/

deployments/docker-compose/
├── docker-compose.yml                # frontend, backend-api, backend-worker, postgres
├── nginx/nginx.conf                  # монтируется в контейнер frontend
├── .env.example                      # только placeholder-значения
└── .env                              # реальные значения, не коммитится

scripts/
└── check.sh                          # единая команда проверки готовности (Принцип IV)
```

Состав виджетов повторяет композицию утверждённого дизайна (Принцип VIII): шапка с меню
счёта, полоса капитала, секция позиций и баннер состояния. Ручное обновление вызывается из
трёх мест, поэтому оформлено хуком `features/refresh-now/useRefreshNow.ts`, а не отдельной
кнопкой.

**Structure Decision**: Web application. Backend — один Python-пакет `backend/` с двумя
точками входа, разворачиваемый как два контейнера (обоснование — research §8): границы
архитектурной диаграммы соблюдены (`Backend-API` не обращается к T-Bank), но без
преждевременного разделения на два проекта с общей библиотекой. Frontend — отдельный
проект на FSD.

---

## 1. Архитектурный поток

Фоновая синхронизация:

```text
APScheduler ❌ → asyncio-цикл в Backend-Worker
        ↓ интервал читается из PostgreSQL каждый цикл
T-Bank Invest API → Backend-Worker → PostgreSQL → Backend-API → Nginx → Frontend
```

Ручное обновление:

```text
Frontend → Backend-API → (внутренний REST) Backend-Worker → T-Bank Invest API
                                                    ↓
                                              PostgreSQL
```

Отображение (независимо от того, чем вызвано обновление):

```text
Frontend (TanStack Query, refetchInterval = clamp(интервал/10, 3 c, 30 c))
        → Backend-API → PostgreSQL
```

Новые контейнеры не добавляются. Daily ML и Intraday Execution ML не создаются.

## 2. Роли контейнеров

### Frontend

React 19 + TypeScript + Vite, Feature-Sliced Design, TanStack Query (server state) и
TanStack Table (сортировка позиций, FR-018). TanStack Router / Form / Start не
используются — обоснование в research §9.

Отвечает за: отображение сводных показателей и таблицы позиций, возраста данных и времени
последней синхронизации, всех семи состояний раздела, ручное обновление, настройку
интервала. Русская локаль и формат денег (FR-016) — на стороне frontend.

Работает только с `Backend-API`. Ничего о состоянии брокера не вычисляет: `is_stale`,
статус синхронизации и статус подключения приходят готовыми.

### Nginx — runtime frontend-контейнера

Отдельного сервиса `nginx` нет: nginx является runtime-слоем образа frontend.
`frontend/Dockerfile` — multi-stage: Node собирает статику, `nginx:alpine` её отдаёт и
проксирует `/api` → `Backend-API`. Конфиг лежит в
`deployments/docker-compose/nginx/nginx.conf` и монтируется в контейнер, поэтому правится
без пересборки образа. Бизнес-логики в нём нет; внутренний REST Worker наружу
**не проксируется**.

Роли `Frontend` и `Nginx` с архитектурной диаграммы закрывает один контейнер: второй
проксирующий слой перед одним SPA не даёт ничего, кроме лишнего сервиса и volume
(Принцип II). Разделение вернётся, когда за Nginx появится более одного upstream'а.

### Backend-API

Python 3.12 + FastAPI + Pydantic v2. Отдаёт состояние счёта, статус и время последней
синхронизации, статус подключения брокера, текущий интервал; принимает изменение интервала
и команду ручного обновления, транслируя её в `Backend-Worker` по внутреннему REST.
К T-Bank не обращается и токена не получает.

### Backend-Worker

Python 3.12 + FastAPI (внутренний REST) + asyncio-цикл синхронизации + SDK
`t-tech-investments` + SQLAlchemy. Единственный контейнер, которому передаётся
`TBANK_INVEST_READ_TOKEN`.

Автоматическая и ручная синхронизация используют **одну функцию** `sync_account_state()`
под общим локом.

### PostgreSQL

Хранит последнее успешное состояние, статус последней попытки синхронизации и настройку
интервала. История снимков не ведётся (не требуется спекой), но схема её не исключает.
Миграции — Alembic. Подробности — [data-model.md](./data-model.md).

## 3. Интеграция с T-Bank

`t-tech-investments==1.49.3`, импорт `from t_tech.invest import AsyncClient`.

⚠️ **Ключевая поправка к черновику**: пакет **не устанавливается с публичного PyPI** — там
он на карантине и не содержит файлов. Обязателен дополнительный индекс:

```text
--extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
```

Это требование к Dockerfile backend и к CI. Детали и проверка — research §1.

Токен доступен только контейнеру `Backend-Worker`, только read-only (FR-005).
Целевой контур — боевой (`INVEST_GRPC_API`); песочница `INVEST_GRPC_API_SANDBOX`
используется для ручной проверки, но не в автотестах.

## 4. Синхронизация

**Автоматическая**: asyncio-цикл в lifespan Worker. Каждый цикл читает
`account_refresh_settings.interval_seconds` из БД, поэтому изменение интервала применяется
к следующему циклу без перезапуска (SC-012), а наложение циклов исключено структурно
(FR-033).

**Ручная**: `Frontend → POST /api/portfolio/refresh → Backend-API → POST /internal/sync →
sync_account_state()`. Если синхронизация уже идёт, второй запрос не создаёт обращения к
брокеру, а дожидается текущего и возвращает его результат с `deduplicated: true`
(FR-029, US2 AS5, US3 AS3).

## 5. Работа с данными

DTO SDK не покидают пакет `financial_ai.broker`. Преобразование:

```text
t_tech.invest models → financial_ai.domain (Decimal) → PostgreSQL (NUMERIC(28,9))
                                                    → Backend-API (JSON, числа строками)
```

`Quotation`/`MoneyValue` (`units` + `nano`) переводятся в `Decimal` как
`units + nano / 1_000_000_000`. `float` не используется нигде на этом пути — обоснование и
последствия для SC-002/SC-003 в research §5.

Backend-API работает только с внутренней моделью. Замена брокера потребует нового адаптера,
реализующего `broker/protocol.py`, и ничего больше.

## 6. Ошибка обновления и обрыв связи

При неуспешном обращении к брокеру сохранённое состояние **не изменяется**; обновляется
только `broker_sync_state` (статус, классифицированная причина, `last_attempt_at`) —
FR-008. Backend-API отдаёт данные и статус синхронизации одним ответом.

Различение причин (FR-037):

| Причина | Признак | Состояние UI |
|---|---|---|
| Нет связи с сервером Financial AI | запрос к `/api/*` не завершился или 502/503/504 | «Нет связи с сервером» + автоповтор + ручной повтор |
| T-Bank API недоступен / вернул ошибку | 200, `sync.status = "failed"` | «Не удалось обновить портфель» + время последней успешной синхронизации |
| Данные устарели | 200, `sync.is_stale = true` | «Данные устарели» + возраст |
| Токен не задан или отклонён | 200, `broker.status ≠ "connected"` | «Брокер не подключён» |

`is_stale` вычисляется backend по FR-040: `max(3 × интервал, 180 с)`.

## 7. Docker и конфигурация

```text
frontend/Dockerfile                   # multi-stage: node build → nginx:alpine
backend/Dockerfile                    # один образ → два контейнера: backend-api и backend-worker
deployments/docker-compose/
├── docker-compose.yml
├── nginx/nginx.conf
├── .env.example
└── .env                              # не коммитится
```

Dockerfile каждого компонента лежит рядом с его кодом; compose, конфиг nginx и переменные
окружения — в `deployments/` (Принцип V).

Compose поднимает `frontend`, `backend-api`, `backend-worker`, `postgres`.
Единственная точка входа снаружи — `frontend` (nginx внутри него).

`TBANK_INVEST_READ_TOKEN` передаётся **только** сервису `backend-worker`.
`.env.example` содержит только placeholder'ы. Реальный `.env` не коммитится
(`.gitignore` уже покрывает `.env` и `.env.*` с исключением `!.env.example`).

Реальный `.env` — **`deployments/docker-compose/.env`**, рядом с compose, который его
читает (решение владельца проекта от 2026-08-26). Существующий корневой `.env` переносится
туда и удаляется из корня при создании deployment-структуры.

Локальная разработка ведётся без Nginx: `vite dev` проксирует `/api` на `Backend-API`
напрямую. Compose с nginx-контейнером используется для проверки, близкой к проду.

## 8. Стек: итоговая таблица

| Задача | Решение | Изменение относительно черновика |
|---|---|---|
| T-Bank integration | `t-tech-investments==1.49.3` + индекс T-Bank | ⚠️ добавлен обязательный extra-index-url |
| Backend HTTP API | FastAPI | без изменений |
| Internal Worker API | FastAPI | без изменений |
| Validation / schemas | Pydantic v2 + pydantic-settings | добавлен pydantic-settings |
| ORM | SQLAlchemy 2.0 async | уточнена версия и async |
| DB migrations | Alembic | без изменений |
| DB driver | asyncpg | добавлено (в черновике «PostgreSQL driver») |
| Background schedule | **asyncio-цикл** | ❌ APScheduler убран (research §2) |
| API → Worker | httpx | добавлено |
| PostgreSQL | PostgreSQL 17 | уточнено: в проекте его ещё нет, разворачивается впервые |
| Frontend server state | TanStack Query | без изменений |
| Frontend таблица | TanStack Table | добавлено (FR-018) |
| Frontend роутинг/формы | — | TanStack Router/Form явно не используются |
| UI | React 19 + TS + Vite + FSD + Open Design | уточнено: стека в репозитории нет, создаётся впервые |
| Python package manager | uv | добавлено |
| Frontend package manager | pnpm | добавлено |
| Quality gates | ruff, mypy, pytest / eslint, tsc, vitest | ❌ отсутствовали в черновике (Принцип IV) |

---

## 9. Согласованные решения

Технический анализ черновика и решения владельца проекта от 2026-08-26.
Все пункты закрыты, кроме двух вопросов размещения конфигурации в конце раздела.

### Исправлено в черновике

| # | Что было | Решение |
|---|---|---|
| 1 | APScheduler для фоновой синхронизации | **Убран.** Одна asyncio-задача: цикл читает интервал из БД, вызывает `sync_account_state()`, ждёт интервал. Запрет наложения циклов обеспечивается структурой цикла, а не параметрами планировщика |
| 2 | `pip install t-tech-investments` с публичного PyPI | **Не работает** — проект на PyPI в статусе quarantined, файлов нет. Обязателен `--extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`, версия пинится `==1.49.3` |
| 3 | «официальный Python SDK» | **Формулировка уточнена**: «рекомендованный в документации T-Bank SDK» — README библиотеки отрицает официальность выпуска. На выбор не влияет |
| 4 | «существующий стек проекта» | **Ничего из перечисленного в репозитории нет.** Все пункты стека — первичные решения этого плана. Отдельно: `AGENTS.md` ссылается на отсутствующий `source/Diagramma_koneynerov_FinAI.svg` |

### Добавлено к черновику

| # | Требование | Принятое решение |
|---|---|---|
| 5 | FR-031, SC-012 — интервал применяется без перезапуска | Worker **читает `interval_seconds` из БД в начале каждого цикла**. Стандартная практика config polling: один источник истины, задержка не более одного цикла, отсутствует состояние «БД обновлена, планировщик — нет» |
| 6 | FR-029, FR-033, US2 AS5, US3 AS3 | **Одновременно может выполняться только одна синхронизация состояния счёта.** Повторный запрос во время текущей синхронизации не запускает второй broker request, а использует результат уже выполняющейся операции (`deduplicated: true`). Реализация: `asyncio.Lock` + PostgreSQL advisory lock |
| 7 | SC-002, SC-003 — денежная точность | Все значения, приходящие из T-Invest как `units` + `nano`, преобразуются и обрабатываются **в доменном слое как `Decimal` без промежуточного `float`**. В PostgreSQL — точные NUMERIC-типы с сохранением точности до 10⁻⁹, по умолчанию **`NUMERIC(28,9)`**. Во внешнем JSON API decimal-значения передаются **строками**. Округление до копеек — только там, где этого требует представление или бизнес-правило |
| 8 | SC-010 — свежесть отображаемого состояния | **Worker ходит к брокеру раз в настроенный интервал; frontend опрашивает уже сохранённое состояние с `refetchInterval = clamp(интервал / 10, 3 с, 30 с)`.** Два интервала разведены явно |
| 9 | Принцип IV Constitution | **Тесты обязательны.** ruff + mypy + pytest (backend), eslint + tsc + vitest (frontend), `alembic upgrade head` на чистой БД. Брокер тестируется на границе адаптера `broker/protocol.py`, а не мокированием gRPC |
| 10 | Принцип III — расхождения с дизайном | **Выполнено 2026-08-26.** Дизайн обновлён в Open Design по заданию [design-update-prompt.md](./design-update-prompt.md); результат проверен — research §10 |
| 11 | FR-010 — привязка состояния к пользователю | **Пользователя как сущности пока нет — есть только токен в конфигурации сервера.** Таблица пользователей и поле `user_id` не вводятся до появления аутентификации. FR-010 выполняется тем, что установка обслуживает один счёт и одного владельца токена |

### Размещение конфигурации и образов

| # | Вопрос | Решение |
|---|---|---|
| 12 | Где лежит `.env` с токеном | **`deployments/docker-compose/.env`** — рядом с compose, который его читает. Корневой `.env` переносится туда и удаляется из корня |
| 13 | Nginx: отдельный контейнер или runtime frontend | **Runtime frontend-образа.** `frontend/Dockerfile` — multi-stage: node build → `nginx:alpine`; конфиг `deployments/docker-compose/nginx/nginx.conf` монтируется в контейнер. Отдельного сервиса `nginx` в compose нет |
| 14 | Где лежат Dockerfile | **Рядом с кодом компонента**: `frontend/Dockerfile`, `backend/Dockerfile`. В `deployments/` — только compose, конфиг nginx и переменные окружения |
| 15 | Nginx в локальной разработке | Не используется: `vite dev` проксирует `/api` на Backend-API. Compose — для проверки, близкой к проду |

---

## Complexity Tracking

Заполняется только при нарушениях Constitution Check. Нарушений нет; ниже — решения,
которые выглядят как усложнение, но им не являются.

| Решение | Почему нужно | Почему более простой вариант отклонён |
|---|---|---|
| Два контейнера backend из одного пакета | Архитектурная диаграмма требует разделения Worker и API; токен должен быть только у Worker | Один контейнер нарушил бы границу «Backend-API не ходит в T-Bank»; два независимых пакета потребовали бы третьего — общей библиотеки |
| Внутренний REST у Worker | Ручная синхронизация должна выполняться тем же процессом, что владеет локом и токеном | Прямой вызов из API невозможен (другой контейнер); флаг в БД + опрос дают задержку до интервала и не удовлетворяют SC-004 |
| PostgreSQL advisory lock поверх `asyncio.Lock` | Страховка при нескольких репликах Worker и перекрытии деплоев | Только in-process lock ломается на второй реплике; очередь задач — новый брокер сообщений ради одной задачи |
| Два интервала (синхронизация и опрос) | SC-010 говорит об отображаемом состоянии, а не о частоте обращений к брокеру | Один интервал даёт задержку показа до двух интервалов; SSE/WebSocket — новый транспорт, избыточен для первой поставки |
