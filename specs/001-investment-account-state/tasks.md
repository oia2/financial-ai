---

description: "Task list for Investment Account State"
---

# Tasks: Investment Account State

**Input**: Design documents from `specs/001-investment-account-state/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: включены — этого требует Принцип IV Constitution, и владелец проекта подтвердил
необходимость тестов при согласовании плана.

**Organization**: задачи сгруппированы по user story, чтобы каждую можно было реализовать и
проверить независимо.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно выполнять параллельно (разные файлы, нет зависимостей)
- **[Story]**: к какой user story относится задача (US1, US2, US3, US4, US5)
- В описании каждой задачи указан точный путь к файлу

## Path Conventions

Проект — web application (см. plan.md → Project Structure):

- backend: `backend/src/financial_ai/`, `backend/tests/`
- frontend: `frontend/src/` (Feature-Sliced Design), `frontend/tests/`
- развёртывание: `deployments/docker-compose/`

Репозиторий greenfield: кода нет, все файлы ниже создаются впервые.

**Дизайн уже обновлён** в Open Design 2026-08-26 по заданию
[design-update-prompt.md](./design-update-prompt.md) и проверен (research §10), поэтому
отдельной задачи на правку дизайна здесь нет.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: инициализация проектов, инструментов качества и развёртывания

- [X] T001 Создать структуру каталогов `backend/src/financial_ai/`, `backend/tests/{unit,integration,contract}/`, `frontend/src/`, `frontend/tests/`, `deployments/docker-compose/nginx/` согласно plan.md → Project Structure
- [X] T002 Создать `backend/pyproject.toml`: Python 3.12, зависимости (fastapi, pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic, httpx, `t-tech-investments==1.49.3`), `extra-index-url = https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`, конфигурация ruff, mypy и pytest в одном файле
- [X] T003 [P] Создать `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`: React 19, TypeScript, TanStack Query, TanStack Table, zod; dev-прокси `/api` на `http://localhost:8001`
- [X] T004 [P] Создать конфигурации качества frontend: `frontend/eslint.config.js`, `frontend/.prettierrc`, `frontend/vitest.config.ts` с Testing Library и msw
- [X] T005 [P] Создать `backend/Dockerfile`: один образ для `backend-api` и `backend-worker`, команда запуска задаётся в compose, установка зависимостей через uv с индексом T-Bank
- [X] T006 [P] Создать `frontend/Dockerfile`: multi-stage — Node 22 собирает статику, runtime `nginx:alpine`
- [X] T007 [P] Создать `deployments/docker-compose/nginx/nginx.conf`: отдача статики SPA с fallback на `index.html`, `proxy_pass /api` на `backend-api:8000`, `Cache-Control: no-store` для `/api`
- [X] T008 Создать `deployments/docker-compose/docker-compose.yml`: сервисы `frontend`, `backend-api`, `backend-worker`, `postgres` (PostgreSQL 17); `TBANK_INVEST_READ_TOKEN` передаётся **только** в `backend-worker`; конфиг nginx монтируется в `frontend`; healthcheck для каждого сервиса
- [X] T009 Создать `deployments/docker-compose/.env.example` с placeholder-значениями, перенести реальное значение из корневого `.env` в `deployments/docker-compose/.env` и удалить корневой `.env`
- [X] T010 [P] Создать `backend/tests/conftest.py`: фикстуры тестовой БД, асинхронного клиента и фейкового брокер-адаптера

**Checkpoint**: проекты инициализированы, `docker compose config` валиден, инструменты качества запускаются

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: инфраструктура, без которой не может быть реализована ни одна user story

**⚠️ CRITICAL**: ни одна user story не начинается до завершения этой фазы

- [X] T011 Реализовать `backend/src/financial_ai/config.py` на pydantic-settings: `DATABASE_URL`, `TBANK_INVEST_READ_TOKEN` (опциональный, читается только worker'ом), `WORKER_INTERNAL_URL`; валидация на старте, `repr` без значений секретов
- [X] T012 [P] Реализовать `backend/src/financial_ai/logging.py`: структурированное JSON-логирование и фильтр, вырезающий значение токена из сообщений и трейсбеков (FR-030, SC-009)
- [X] T013 Реализовать `backend/src/financial_ai/db/engine.py`: async engine, session factory, зависимость для FastAPI
- [X] T014 Реализовать `backend/src/financial_ai/db/models.py`: таблицы `investment_account`, `account_state`, `portfolio_position`, `broker_sync_state`, `account_refresh_settings` строго по [data-model.md](./data-model.md) — `NUMERIC(28,9)` для денежных и количественных величин, `TIMESTAMPTZ` для времени, CHECK-ограничения singleton и диапазона интервала
- [X] T015 Настроить Alembic: `backend/alembic.ini`, `backend/migrations/env.py`, первая миграция в `backend/migrations/versions/` — создание всех пяти таблиц и seed-строки `account_refresh_settings` со значением 60
- [X] T016 [P] Создать `backend/src/financial_ai/api/app.py`: FastAPI-приложение Backend-API, подключение логирования, роутер `backend/src/financial_ai/api/routes/health.py` с `GET /api/health`
- [X] T017 [P] Создать `backend/src/financial_ai/worker/app.py`: FastAPI-приложение Worker, роутер `backend/src/financial_ai/worker/routes/health.py` с `GET /internal/health`, возвращающим `broker_token: configured|missing` — **факт наличия, никогда не значение**
- [X] T018 [P] Создать `frontend/src/app/`: провайдер `QueryClient`, глобальные стили и CSS-токены из `brand-spec.md` (`--bg`, `--surface`, `--fg`, `--muted`, `--border`, `--accent`), типографика Google Sans Flex / Google Sans Code
- [X] T019 [P] Создать `frontend/src/shared/api/client.ts`: HTTP-клиент, различающий транспортную ошибку (нет ответа, 502/503/504) и успешный ответ с телом — основа для FR-037
- [X] T020 [P] Написать тесты health-эндпоинтов в `backend/tests/contract/test_health.py`: `GET /api/health` и `GET /internal/health`, проверка отсутствия значения токена в ответе

**Checkpoint**: контейнеры поднимаются, миграции применяются, health-эндпоинты отвечают — можно начинать user stories

---

## Phase 3: User Story 1 - Просмотр актуального состояния инвестиционного счёта (Priority: P1) 🎯 MVP

**Goal**: пользователь открывает раздел «Портфель» и видит фактическое состояние счёта из
T-Bank: общую стоимость, денежные средства с долей, нереализованный P&L, список позиций и
момент актуальности данных.

**Independent Test**: выполнить одноразовую синхронизацию (`python -m financial_ai.sync.cli`),
открыть раздел «Портфель» и сверить каждое отображённое значение с ответом T-Bank API;
сумма стоимостей позиций и денежных средств равна общей стоимости, сумма долей — 100%.

### Tests for User Story 1 ⚠️

> Тесты пишутся ПЕРВЫМИ и должны падать до реализации

- [X] T021 [P] [US1] Контрактный тест `GET /api/portfolio` в `backend/tests/contract/test_portfolio_get.py`: структура ответа по [contracts/backend-api.md](./contracts/backend-api.md), денежные значения — строки, `snapshot: null` при отсутствии синхронизации
- [X] T022 [P] [US1] Unit-тесты доменных расчётов в `backend/tests/unit/test_portfolio_calc.py`: доли позиций и денежных средств, % P&L, граничные случаи из спеки — `total_value = 0`, нулевая база P&L (`null`, а не ложный ноль), отрицательное количество, отсутствующая `average_price`
- [X] T023 [P] [US1] Unit-тесты маппинга T-Invest в `backend/tests/unit/test_tinvest_mapping.py`: перевод `Quotation` и `MoneyValue` (`units` + `nano`) в `Decimal` на граничных значениях — нулевые, отрицательные, максимальные `nano`, длинная дробная часть; ожидаемые значения задаются как `Decimal`, сравнение с `float` запрещено (SC-002)
- [X] T024 [P] [US1] Unit-тесты валидации ответа брокера в `backend/tests/unit/test_broker_validation.py`: несогласованные суммы, отсутствующие обязательные поля и пустой ответ отклоняются до записи в БД (FR-004)
- [X] T025 [P] [US1] Unit-тесты сериализации в `backend/tests/unit/test_schema_serialization.py`: `Decimal` отдаётся строкой без потери точности и без экспоненциальной записи; процентные поля отдают `null`, а не ложный ноль, при нулевой базе
- [X] T026 [P] [US1] Unit-тесты форматирования в `frontend/tests/format.test.ts`: разделители разрядов русской локали, округление до копеек только при отображении, знак P&L, разбор строковых чисел из API без потери точности (FR-016)
- [X] T027 [P] [US1] Интеграционный тест синхронизации в `backend/tests/integration/test_sync_success.py`: успешный ответ фейкового брокера → атомарная запись `account_state` + `portfolio_position` + `broker_sync_state` в одной транзакции (FR-008)
- [X] T028 [P] [US1] Тест страницы портфеля в `frontend/tests/portfolio-page.test.tsx` с msw: отображение сводных показателей, таблицы позиций, возраста данных и времени последней синхронизации

### Implementation for User Story 1

- [X] T029 [P] [US1] Реализовать `backend/src/financial_ai/domain/models.py`: доменные модели счёта, снимка и позиции на `Decimal`, без `float`
- [X] T030 [US1] Реализовать `backend/src/financial_ai/domain/portfolio.py`: расчёт долей, стоимости, абсолютного и процентного P&L, `positions_cost_basis`, возраста данных — по правилам [data-model.md §9](./data-model.md)
- [X] T031 [P] [US1] Реализовать `backend/src/financial_ai/broker/protocol.py`: протокол брокер-адаптера (получение счёта и портфеля) и фейковая реализация в `backend/tests/fakes/fake_broker.py` для тестов
- [X] T032 [US1] Реализовать `backend/src/financial_ai/broker/tinvest.py`: адаптер `t_tech.invest.AsyncClient` → доменные модели, перевод `Quotation`/`MoneyValue` (`units` + `nano`) в `Decimal` как `units + nano / 1_000_000_000`, маскирование номера договора, выбор единственного счёта (FR-025)
- [X] T033 [US1] Реализовать `backend/src/financial_ai/sync/service.py`: `sync_account_state()` — получение состояния, валидация полноты и согласованности (FR-004), атомарная запись по транзакции из [data-model.md §7](./data-model.md)
- [X] T034 [US1] Реализовать `backend/src/financial_ai/sync/cli.py`: одноразовый запуск синхронизации (`python -m financial_ai.sync.cli`) — точка независимой проверки этой story до появления планировщика
- [X] T035 [US1] Реализовать `backend/src/financial_ai/api/schemas.py`: Pydantic-схемы ответа `GET /api/portfolio`, сериализация `Decimal` в строку, `Cache-Control: no-store`
- [X] T036 [US1] Реализовать `backend/src/financial_ai/api/routes/portfolio.py`: `GET /api/portfolio` — чтение сохранённого состояния, сборка блоков `broker`, `snapshot`, `sync` без обращения к брокеру
- [X] T037 [P] [US1] Реализовать `frontend/src/entities/portfolio/`: типы ответа API, `usePortfolioQuery`, селекторы состояния раздела
- [X] T038 [P] [US1] Реализовать `frontend/src/shared/lib/format.ts`: форматирование денег, количеств и процентов под русскую локаль с разделителями разрядов (FR-016), округление до копеек только при отображении
- [X] T039 [US1] Реализовать `frontend/src/widgets/portfolio-summary/`: общая стоимость, денежные средства с долей, P&L в рублях и процентах с визуальным различением знака (FR-013), количество позиций
- [X] T040 [US1] Реализовать `frontend/src/widgets/positions-table/`: таблица позиций со столбцами инструмент, количество, средняя цена, текущая цена, стоимость, P&L, доля; отображение по `instrument_uid` при отсутствии тикера и названия
- [X] T041 [US1] Реализовать `frontend/src/widgets/freshness/`: возраст данных и точное время последней успешной синхронизации (FR-014)
- [X] T042 [US1] Реализовать `frontend/src/pages/portfolio/`: сборка экрана и состояния «загрузка» и «портфель пуст» по утверждённому дизайну

**Checkpoint**: US1 полностью работоспособна — состояние счёта читается из T-Bank, сохраняется и корректно отображается. MVP готов к демонстрации.

---

## Phase 4: User Story 2 - Автоматическое поддержание актуальности состояния счёта (Priority: P2)

**Goal**: состояние счёта обновляется в фоне без участия пользователя; частоту обновления
пользователь меняет в интерфейсе.

**Independent Test**: открыть раздел и, не выполняя действий, убедиться, что отметка
последней синхронизации обновляется не реже раза за настроенный интервал; изменить интервал
в интерфейсе и убедиться, что новая частота применилась без перезапуска.

### Tests for User Story 2 ⚠️

- [X] T043 [P] [US2] Контрактные тесты настройки в `backend/tests/contract/test_settings.py`: `GET`/`PUT /api/settings/refresh-interval`, границы 15–3600, ответ `422 interval_out_of_range` и сохранение прежнего значения при недопустимом вводе
- [X] T044 [P] [US2] Интеграционный тест в `backend/tests/integration/test_scheduler_interval.py`: изменение `interval_seconds` в БД применяется к следующему циклу без перезапуска (SC-012)
- [X] T045 [P] [US2] Интеграционный тест в `backend/tests/integration/test_scheduler_resilience.py`: циклы не накладываются и не накапливаются (FR-033), цикл продолжается после ошибки брокера (FR-032)
- [X] T046 [P] [US2] Тест настройки интервала в `frontend/tests/refresh-interval.test.tsx`: ввод вне диапазона отклоняется с объяснением, прежний интервал продолжает действовать, допустимое значение сохраняется
- [X] T047 [P] [US2] Unit-тест poll-интервала в `frontend/tests/poll-interval.test.ts`: `clamp(refresh_interval_seconds / 10, 3 c, 30 c)` на границах диапазона 15–3600 и при промежуточных значениях (research §6)

### Implementation for User Story 2

- [X] T048 [P] [US2] Реализовать `backend/src/financial_ai/db/settings_repo.py`: чтение и обновление `account_refresh_settings` с проверкой диапазона
- [X] T049 [US2] Реализовать `backend/src/financial_ai/api/routes/settings.py`: `GET`/`PUT /api/settings/refresh-interval`, возврат границ `min_seconds`/`max_seconds`/`default_seconds`, ошибка `interval_out_of_range`
- [X] T050 [US2] Реализовать `backend/src/financial_ai/sync/lock.py`: `asyncio.Lock` вокруг `sync_account_state()`, исключающий параллельные фоновые обращения к брокеру
- [X] T051 [US2] Реализовать `backend/src/financial_ai/sync/scheduler.py`: asyncio-цикл — чтение интервала из БД в начале каждого цикла, вызов синхронизации под локом, ожидание с возможностью корректной остановки
- [X] T052 [US2] Подключить планировщик в `backend/src/financial_ai/worker/app.py`: запуск в lifespan startup, остановка с дожиданием текущей синхронизации в shutdown; отражение состояния в `GET /internal/health` (`scheduler`, `current_interval_seconds`)
- [X] T053 [P] [US2] Реализовать `frontend/src/features/refresh-interval-setting/`: форма ввода целого числа секунд с подсказкой диапазона, валидацией и сообщением об ошибке, мутация `PUT /api/settings/refresh-interval`
- [X] T054 [US2] Настроить polling в `frontend/src/entities/portfolio/`: `refetchInterval = clamp(refresh_interval_seconds / 10, 3 c, 30 c)` от значения, пришедшего в ответе API (research §6)
- [X] T055 [US2] Отобразить действующий интервал в `frontend/src/widgets/account-menu/` и в шапке раздела (FR-036)
- [X] T056 [US2] Обеспечить в `frontend/src/widgets/positions-table/` сохранение выбранной сортировки и позиции прокрутки при фоновом обновлении данных (US2 AS2)

**Checkpoint**: US1 и US2 работают независимо — данные обновляются сами, частота настраивается

---

## Phase 5: User Story 3 - Обновление состояния счёта по требованию (Priority: P2)

**Goal**: пользователь нажимает «Обновить сейчас» и получает свежие данные с новой отметкой
времени; повторное нажатие во время выполнения не создаёт второго обращения к брокеру.

**Independent Test**: на отображённом состоянии выполнить обновление и убедиться, что
значения и отметка времени пересчитаны; два одновременных запроса дают один broker request,
у одного из ответов — `deduplicated: true`.

### Tests for User Story 3 ⚠️

- [X] T057 [P] [US3] Контрактный тест `POST /api/portfolio/refresh` в `backend/tests/contract/test_portfolio_refresh.py`: успех, `status: failed` при ошибке брокера, `503 worker_unavailable` при недоступном worker'е
- [X] T058 [P] [US3] Контрактный тест `POST /internal/sync` в `backend/tests/contract/test_worker_sync.py`: 200 с `status: failed` вместо 5xx при ошибке брокера, поля `deduplicated` и `duration_ms`
- [X] T059 [P] [US3] Интеграционный тест в `backend/tests/integration/test_sync_dedup.py`: одновременные ручная и фоновая синхронизации → ровно одно обращение к брокеру, второй запрос получает результат первого (FR-029)
- [X] T060 [P] [US3] Тест кнопки обновления в `frontend/tests/refresh-now.test.tsx`: индикация загрузки, обновлённые значения и отметка времени, блокировка повторного запуска до завершения

### Implementation for User Story 3

- [X] T061 [US3] Реализовать `backend/src/financial_ai/worker/routes/sync.py`: `POST /internal/sync` — вызов той же `sync_account_state()`, что использует планировщик, ответ по [contracts/worker-internal-api.md](./contracts/worker-internal-api.md)
- [X] T062 [US3] Дополнить `backend/src/financial_ai/sync/lock.py`: PostgreSQL advisory lock поверх `asyncio.Lock` и семантика `deduplicated` — ожидание текущей операции вместо второго обращения к брокеру, ограниченное таймаутом
- [X] T063 [US3] Реализовать `POST /api/portfolio/refresh` в `backend/src/financial_ai/api/routes/portfolio.py`: httpx-вызов `WORKER_INTERNAL_URL`, трансляция результата, `503 worker_unavailable` при недоступности worker'а
- [X] T064 [P] [US3] Реализовать `frontend/src/features/refresh-now/`: кнопка «Обновить сейчас», индикация выполнения, защита от повторного запуска, инвалидация query после завершения
- [X] T065 [US3] Добавить подтверждение успешного обновления в `frontend/src/shared/ui/toast/` и подключить его к сценарию ручного обновления (US3 AS2)

**Checkpoint**: US1–US3 работают независимо; автоматическое и ручное обновление используют один код синхронизации

---

## Phase 6: User Story 4 - Прозрачность актуальности данных при сбоях брокера и обрыве связи (Priority: P3)

**Goal**: при недоступности T-Bank API или обрыве связи с сервером пользователь видит
последнее известное состояние и предупреждение, однозначно называющее причину.

**Independent Test**: смоделировать по отдельности недоступность T-Bank API и остановку
`backend-api`; убедиться, что в каждом случае показано последнее известное состояние с
предупреждением, а сообщения различаются и указывают действительную причину.

### Tests for User Story 4 ⚠️

- [X] T066 [P] [US4] Unit-тесты классификации ошибок в `backend/tests/unit/test_broker_errors.py`: `broker_unavailable`, `broker_rejected_token`, `rate_limited`, `validation_failed`, `internal_error`
- [X] T067 [P] [US4] Интеграционный тест в `backend/tests/integration/test_sync_failure.py`: неуспешная синхронизация обновляет только `broker_sync_state`, сохранённое состояние и позиции не изменяются (FR-008, US4 AS3)
- [X] T068 [P] [US4] Unit-тест `is_stale` в `backend/tests/unit/test_staleness.py`: порог `max(3 × интервал, 180 с)` по FR-040, отсутствие снимка не считается устареванием
- [X] T069 [P] [US4] Тесты состояний в `frontend/tests/sync-states.test.tsx`: три различимых предупреждения (устаревание, сбой брокера, нет связи с сервером), автоматическое снятие предупреждения после восстановления (FR-039, SC-011)
- [X] T070 [P] [US4] Тест в `backend/tests/integration/test_no_secret_leak.py`: значение токена не появляется в ответах API, логах и сообщениях об ошибках при всех классах сбоев (SC-009)

### Implementation for User Story 4

- [X] T071 [US4] Реализовать `backend/src/financial_ai/broker/errors.py`: классификация исключений SDK и сетевых ошибок в коды `failure_reason_code`, санитизация диагностики
- [X] T072 [US4] Дополнить `backend/src/financial_ai/sync/service.py`: транзакция неуспеха по [data-model.md §8](./data-model.md) — обновление статуса, причины, `consecutive_failures`, перевод `broker_status` в `rejected` при отклонённом токене; `not_configured` при отсутствующем токене
- [X] T073 [US4] Дополнить `backend/src/financial_ai/api/routes/portfolio.py` и `schemas.py`: блоки `broker.status`, `sync.status`, `sync.failure_reason_code`, `sync.is_stale`, `sync.stale_after_seconds`, `sync.in_progress`
- [X] T074 [P] [US4] Реализовать `frontend/src/widgets/sync-status-banner/`: варианты «Данные временно устарели», «Не удалось обновить портфель» и «Нет связи с сервером Financial AI» по утверждённому дизайну, с действием повтора
- [X] T075 [US4] Реализовать в `frontend/src/entities/portfolio/` и `frontend/src/shared/api/client.ts` обработку транспортной ошибки: состояние «нет связи с сервером», автоматические повторы подключения, немедленный повтор по действию пользователя, автоматическое снятие после восстановления (FR-038, FR-039)
- [X] T076 [US4] Реализовать в `frontend/src/pages/portfolio/` состояние «Брокер не подключён» для `broker.status` `not_configured` и `rejected`: пояснение о несконфигурированном доступе без раскрытия токена (FR-020, FR-024)
- [X] T077 [US4] Подключить логирование причин неуспешной синхронизации в `backend/src/financial_ai/sync/service.py` через фильтр секретов из `logging.py` (FR-030)

**Checkpoint**: все причины несвежести данных различимы; сохранённое состояние никогда не затирается сбоем

---

## Phase 7: User Story 5 - Разбор состава портфеля (Priority: P3)

**Goal**: пользователь сортирует таблицу позиций по любому столбцу, чтобы понять структуру
капитала.

**Independent Test**: переключить сортировку по каждому поддерживаемому столбцу и проверить
корректность порядка, индикацию направления и сортировку по умолчанию.

### Tests for User Story 5 ⚠️

- [X] T078 [P] [US5] Тест сортировки в `frontend/tests/positions-sorting.test.tsx`: сортировка по каждому столбцу, переключение направления повторным выбором, порядок по умолчанию — убывание доли в портфеле

### Implementation for User Story 5

- [X] T079 [US5] Подключить TanStack Table в `frontend/src/widgets/positions-table/`: сортировка по всем отображаемым столбцам с переключением направления и индикацией (FR-018)
- [X] T080 [US5] Задать сортировку по умолчанию — убывание доли в портфеле — в `frontend/src/widgets/positions-table/`, с корректной обработкой `null` в столбцах P&L и средней цены

**Checkpoint**: все пять user stories функционально завершены

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: сквозные требования и приёмка

- [X] T081 [P] Проверить и довести адаптивность раздела в `frontend/src/pages/portfolio/` и `frontend/src/widgets/positions-table/` на мобильных экранах (FR-019)
- [X] T082 [P] Измерить и зафиксировать показатели SC-001 (отрисовка < 2 с) и SC-004 (ручное обновление ≤ 5 с), при необходимости оптимизировать запрос в `backend/src/financial_ai/api/routes/portfolio.py`
- [X] T083 [P] Провести дизайн-ревью семи состояний раздела против проекта Open Design «Портфель FINANCIAL AI» (SC-007) и зафиксировать результат в `specs/001-investment-account-state/checklists/`
- [X] T084 Выполнить все проверочные сценарии из [quickstart.md](./quickstart.md) §4 на поднятом compose и зафиксировать результаты
- [X] T085 [P] Выполнить проверку безопасности из [quickstart.md](./quickstart.md) §6: отсутствие токена в логах и ответах, отсутствие полного номера договора, `.env` не отслеживается git (SC-009, FR-022)
- [X] T086 [P] Обновить `README.md`: перевести строку «Код» в статусе из «не реализован» в актуальное состояние, сверить команды запуска с фактическими
- [X] T087 [P] Устранить расхождение в `AGENTS.md`: добавить `source/Diagramma_koneynerov_FinAI.svg` в репозиторий либо убрать ссылку на отсутствующий файл (Принципы III и V)
- [X] T088 Прогнать все quality gates из [quickstart.md](./quickstart.md) §5 до зелёного результата: ruff, mypy, pytest, eslint, prettier, tsc, vitest, `alembic upgrade head` на чистой БД (Принцип IV)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: без зависимостей, начинается сразу
- **Foundational (Phase 2)**: зависит от Phase 1, **блокирует все user stories**
- **User Stories (Phase 3–7)**: зависят от Phase 2; далее — параллельно или последовательно по приоритету
- **Polish (Phase 8)**: зависит от завершения нужных user stories

### User Story Dependencies

- **US1 (P1)**: зависит только от Phase 2. Ни от одной другой story не зависит
- **US2 (P2)**: зависит от Phase 2. Использует `sync_account_state()` из US1 — при параллельной работе синхронизируйтесь по `sync/service.py`
- **US3 (P2)**: зависит от Phase 2. Использует `sync_account_state()` из US1 и `sync/lock.py` из US2 (T062 расширяет T050)
- **US4 (P3)**: зависит от Phase 2. Расширяет `sync/service.py` (US1) и ответ `GET /api/portfolio` (US1); порог устаревания использует интервал из US2
- **US5 (P3)**: зависит от Phase 2 и таблицы позиций из US1 (T040). Полностью frontend

Реальный порядок с наименьшим числом конфликтов: **US1 → US2 → US3 → US4 → US5**.

### Within Each User Story

- Тесты пишутся первыми и падают до реализации
- Доменные модели → расчёты → адаптер брокера → сервис синхронизации → эндпоинты → UI
- Story завершается и проверяется до перехода к следующей

### Parallel Opportunities

- Phase 1: T003–T007 и T010 параллельны (T002 первым — от него зависит T010)
- Phase 2: T012, T016, T017, T018, T019, T020 параллельны после T011 и T013–T015
- Внутри каждой story все тестовые задачи с [P] пишутся параллельно
- Backend и frontend внутри одной story ведутся параллельно после согласования контракта
- US5 полностью независима от backend и может выполняться параллельно с US2–US4

---

## Parallel Example: User Story 1

```bash
# Тесты US1 — параллельно:
Task: "Контрактный тест GET /api/portfolio в backend/tests/contract/test_portfolio_get.py"
Task: "Unit-тесты доменных расчётов в backend/tests/unit/test_portfolio_calc.py"
Task: "Unit-тесты маппинга T-Invest в backend/tests/unit/test_tinvest_mapping.py"
Task: "Unit-тесты валидации ответа брокера в backend/tests/unit/test_broker_validation.py"
Task: "Unit-тесты сериализации в backend/tests/unit/test_schema_serialization.py"
Task: "Интеграционный тест синхронизации в backend/tests/integration/test_sync_success.py"
Task: "Тест страницы портфеля в frontend/tests/portfolio-page.test.tsx"
Task: "Unit-тесты форматирования в frontend/tests/format.test.ts"

# Независимые модули US1 — параллельно:
Task: "Доменные модели в backend/src/financial_ai/domain/models.py"
Task: "Протокол брокер-адаптера в backend/src/financial_ai/broker/protocol.py"
Task: "Типы и query в frontend/src/entities/portfolio/"
Task: "Форматирование под русскую локаль в frontend/src/shared/lib/format.ts"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1: Setup
2. Phase 2: Foundational — блокирует всё остальное
3. Phase 3: US1
4. **STOP и проверить**: `python -m financial_ai.sync.cli`, затем сверить раздел «Портфель» с ответом T-Bank
5. Демонстрация MVP

### Incremental Delivery

1. Setup + Foundational → фундамент готов
2. US1 → проверка → демонстрация (MVP)
3. US2 → данные обновляются сами, частота настраивается
4. US3 → ручное обновление с дедупликацией
5. US4 → честные состояния при сбоях и обрыве связи
6. US5 → сортировка позиций
7. Phase 8 → приёмка по quickstart и quality gates

### Parallel Team Strategy

1. Команда вместе закрывает Setup + Foundational
2. Далее: разработчик A — US1 (критический путь), разработчик B — frontend-часть US2 и US5, разработчик C — US3 после появления `sync/service.py`
3. US4 берётся после US1 и US2, так как расширяет их код

---

## Notes

- [P] = разные файлы, нет зависимостей между задачами
- Каждая задача содержит точный путь к файлу и завершается коммитом
- `float` не используется нигде на пути «брокер → БД → API → JSON»: только `Decimal` и `NUMERIC(28,9)`, в JSON — строки
- Значение `TBANK_INVEST_READ_TOKEN` не попадает в интерфейс, БД, логи и ответы API
- Прототип Open Design хранит интервал в `localStorage` и обновляет данные раз в интервал — **в реализацию это не переносится**: интервал хранится на сервере (FR-034), а фронт опрашивает API с `clamp(интервал / 10, 3 c, 30 c)`
- Дизайн уже обновлён и проверен; отдельной задачи на его правку нет

---

## Phase 9: Convergence

**Purpose**: закрыть расхождения между артефактами фичи и текущим состоянием кода,
обнаруженные прогоном `/speckit-converge` 2026-08-26.

- [X] T089 **CRITICAL** Актуализировать статус в `README.md`: строка «Проверка на реальном брокере» и абзац под таблицей утверждают, что T-Bank отклоняет токен, тогда как система синхронизируется успешно (`broker: connected`, 11 позиций) per Constitution IX (contradicts)
- [X] T090 Внести в `specs/001-investment-account-state/spec.md` требования на поведение таблицы, реализованное по обновлённому дизайну: пагинация с выбором 10/25/50 строк и сохранением выбора, переключатель подсветки строк по знаку P&L с сохранением состояния между сессиями; добавить acceptance-сценарии к User Story 5 per FR-012, FR-018 (unrequested)
- [X] T091 Прогнать на живых данных сценарии quickstart 4.1, 4.2, 4.5, 4.7, 4.8, 4.9, ранее заблокированные отклонённым токеном, и обновить раздел приёмки в `specs/001-investment-account-state/checklists/requirements.md` per US1/AC1, US2, US4, US5, SC-002, SC-005 (contradicts)
- [X] T092 Привести раздел Project Structure в `specs/001-investment-account-state/plan.md` в соответствие с кодом: `widgets/{app-header,capital-strip,positions-table,sync-status-banner}`, `features/refresh-now` как хук, добавленные `shared/lib/{plural,preferences}.ts` per plan: Project Structure (partial)
- [X] T093 [P] Переименовать каталог `frontend/src/widgets/positions-table/` в `positions-section` вслед за компонентом `PositionsSection`, который теперь включает панель инструментов, пагинацию и примечание per Constitution V (partial)

---

## Phase 10: Convergence

**Purpose**: расхождения, найденные вторым прогоном `/speckit-converge` 2026-08-26.
Функциональных пробелов нет — все три касаются проектных артефактов, отставших от кода.

- [X] T094 Внести поле `accrued_interest` в описание позиции в `specs/001-investment-account-state/contracts/backend-api.md`: API отдаёт его и фронтенд им пользуется, в `data-model.md` оно описано, а в контракте отсутствует; добавить пояснение, что НКД включён в `value` per plan: contracts/backend-api.md (contradicts)
- [X] T095 Актуализировать `AGENTS.md`: Nginx перечислен отдельным компонентом, хотя стал runtime-слоем контейнера frontend; добавить `scripts/check.sh` как единую команду проверки готовности per Constitution IX (partial)
- [X] T096 [P] Добавить `scripts/` в дерево Project Structure в `specs/001-investment-account-state/plan.md` per plan: Project Structure, Constitution V (partial)
