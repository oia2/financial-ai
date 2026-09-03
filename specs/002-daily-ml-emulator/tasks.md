---

description: "Task list for feature implementation: Эмулятор Daily ML"
---

# Tasks: Эмулятор Daily ML

**Input**: Design documents from `/specs/002-daily-ml-emulator/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/daily-ml-emulator-api.md](./contracts/daily-ml-emulator-api.md),
[quickstart.md](./quickstart.md)

**Tests**: включены. Это не выбор в пользу TDD, а требование Принципа IV конституции:
новая существенная логика сопровождается тестами, если её возможно проверить
автоматически. Правило ранжирования, валидация конфигурации и контракт ответа
проверяются автоматически полностью.

**Organization**: задачи сгруппированы по user story, чтобы каждую можно было реализовать
и проверить независимо.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно выполнять параллельно (разные файлы, нет незакрытых зависимостей)
- **[Story]**: к какой user story относится задача (US1, US2, US3, US4)
- В описании — точный путь к файлу

## Path Conventions

Структура задана в [plan.md](./plan.md): новая директория верхнего уровня
`daily-ml-emulator/` с собственным uv-проектом, код в `daily-ml-emulator/src/daily_ml_emulator/`,
тесты в `daily-ml-emulator/tests/`. Развёртывание — в `deployments/docker-compose/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: инициализация отдельного uv-проекта эмулятора

- [X] T001 Создать структуру директорий `daily-ml-emulator/` с подкаталогами `src/daily_ml_emulator/`, `tests/`, `universe/` и файлом `daily-ml-emulator/src/daily_ml_emulator/__init__.py`
- [X] T002 Создать `daily-ml-emulator/pyproject.toml`: имя проекта `daily-ml-emulator`, `requires-python = ">=3.12"`, зависимости `fastapi`, `uvicorn[standard]`, `pydantic>=2.9`, `pydantic-settings`, dev-группа `pytest`, `ruff`, `mypy`. Индекс T-Bank и зависимости `backend/` НЕ переносятся (FR-018)
- [X] T003 Перенести в `daily-ml-emulator/pyproject.toml` конфигурацию инструментов по образцу `backend/pyproject.toml`: `[tool.ruff]` с `line-length = 100`, `target-version = "py312"` и `ignore = ["RUF001", "RUF002", "RUF003"]` (документация и сообщения на русском), `[tool.mypy]` с `files = ["src"]`, `[tool.pytest.ini_options]` с `testpaths = ["tests"]`
- [X] T004 [P] Создать `daily-ml-emulator/.dockerignore` по образцу `backend/.dockerignore`
- [X] T005 Сгенерировать `daily-ml-emulator/uv.lock` командой `uv sync` в `daily-ml-emulator/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: минимум, без которого не работает ни одна user story: конфигурация, загрузка
вселенной, каркас приложения

**⚠️ CRITICAL**: ни одна user story не начинается до завершения этой фазы

- [X] T006 [P] Реализовать `Settings` в `daily-ml-emulator/src/daily_ml_emulator/config.py` через `pydantic-settings`: `DAILY_ML_EMULATOR_MODEL_ID` (по умолчанию `daily-ml-emulator-v1`), `DAILY_ML_EMULATOR_UNIVERSE_PATH` (по умолчанию `/app/universe/default.json`), `LOG_LEVEL` (по умолчанию `INFO`) — по data-model.md §5
- [X] T007 [P] Создать `daily-ml-emulator/universe/default.json`: 10 активов в порядке SBER, GAZP, LKOH, GMKN, ROSN, NVTK, TATN, MTSS, MGNT, CHMF, каждый как `{"asset_id": "EQ_AST_<ТИКЕР>", "price_series_id": "EQ_PRS_<ТИКЕР>"}` — формат обоснован в research.md §4
- [X] T008 Реализовать модель `UniverseEntry` и чтение JSON-файла вселенной в `daily-ml-emulator/src/daily_ml_emulator/universe.py` (порядок записей значим: он задаёт базовую нумерацию для сдвига). Строгая валидация — задача T031 в US4
- [X] T009 Создать каркас приложения в `daily-ml-emulator/src/daily_ml_emulator/app.py`: экземпляр FastAPI, загрузка вселенной при старте в память, настройка логирования по `LOG_LEVEL`
- [X] T010 Добавить в `daily-ml-emulator/src/daily_ml_emulator/app.py` единый обработчик ошибок, приводящий ответы к виду `{"detail": {"code": "...", "message": "..."}}` — форма зафиксирована контрактом первой фичи, второго соглашения проект не вводит (research.md §8)

**Checkpoint**: приложение поднимается, вселенная загружена, ошибки имеют единую форму

---

## Phase 3: User Story 1 - Получение ранжирования на дату решения (Priority: P1) 🎯 MVP

**Goal**: эмулятор принимает дату решения и возвращает упорядоченный список активов со
скорами в форме, которую потом вернёт настоящая модель

**Independent Test**: одним запросом `curl "localhost:8100/rankings?decision_date=2026-08-28"`
к запущенному эмулятору получить непустой упорядоченный ответ и убедиться, что состав
полей соответствует контракту

### Tests for User Story 1

> Тесты пишутся первыми и должны падать до реализации

- [X] T011 [P] [US1] Тесты правила ранжирования в `daily-ml-emulator/tests/test_ranking.py`: сдвиг `offset = date.toordinal() mod N`, непрерывность рангов 1..N, строгое убывание скоров, лестница при N=10 равна `0.9091, 0.8182, 0.7273, 0.6364, 0.5455, 0.4545, 0.3636, 0.2727, 0.1818, 0.0909` (data-model.md §4)
- [X] T012 [P] [US1] Тесты детерминизма в `daily-ml-emulator/tests/test_ranking.py`: повторный вызов на ту же дату даёт тот же результат; результат не зависит от текущего времени; на датах `2026-08-28`, `2026-08-29`, `2026-08-31` первым идут `EQ_AST_ROSN`, `EQ_AST_GMKN`, `EQ_AST_GAZP` соответственно (FR-009, FR-010)
- [X] T013 [P] [US1] Контрактные тесты `GET /rankings` в `daily-ml-emulator/tests/test_api.py` через `fastapi.testclient`: код 200, наличие всех полей ответа из contracts/, `items` содержит всю вселенную, `asset_id` уникален, `score` — строка, а не число (FR-003, FR-007, FR-008)
- [X] T014 [P] [US1] Тесты признака эмуляции в `daily-ml-emulator/tests/test_api.py`: `emulated == true` и непустой `emulation_notice` в каждом успешном ответе (FR-006, SC-005)
- [X] T015 [P] [US1] Тесты ошибок в `daily-ml-emulator/tests/test_api.py`: `decision_date` в формате `28.08.2026`, отсутствующий параметр и пустое значение дают 422 с `detail.code == "invalid_decision_date"`, ранжирование при этом не возвращается (FR-013)

### Implementation for User Story 1

- [X] T016 [US1] Реализовать правило ранжирования в `daily-ml-emulator/src/daily_ml_emulator/ranking.py`: `offset = decision_date.toordinal() % N`, `rank(i) = ((i + offset) % N) + 1`, сортировка по `rank` с вторичным ключом `asset_id`
- [X] T017 [US1] Реализовать лестницу скоров в `daily-ml-emulator/src/daily_ml_emulator/ranking.py`: `score = Decimal(1) - Decimal(rank)/Decimal(N+1)`, квантование до 4 знаков, возврат строкой. `float` на этом пути не использовать (FR-007, research.md §5)
- [X] T018 [P] [US1] Описать схемы ответа `Ranking` и `RankingItem` в `daily-ml-emulator/src/daily_ml_emulator/app.py`: поля и типы по data-model.md §2 и §3, `score` типизирован как `str`
- [X] T019 [US1] Реализовать эндпоинт `GET /rankings` в `daily-ml-emulator/src/daily_ml_emulator/app.py`: единственный параметр `decision_date` типа `datetime.date`, сборка ответа с `model_id` из настроек, `generated_at` в UTC, `emulated: true` и текстом предупреждения (зависит от T016, T017, T018)
- [X] T020 [US1] Привести ошибку валидации `decision_date` к коду `invalid_decision_date` в обработчике из T010, в `daily-ml-emulator/src/daily_ml_emulator/app.py`

**Checkpoint**: User Story 1 полностью работает и проверяется независимо — MVP готов

---

## Phase 4: User Story 2 - Запуск эмулятора как контейнера (Priority: P2)

**Goal**: эмулятор собирается в образ, поднимается контейнером и сообщает о готовности

**Independent Test**: собрать образ, запустить контейнер, дождаться `{"status":"ok"}` на
`/health` и выполнить проверку User Story 1 против запущенного контейнера

### Tests for User Story 2

- [X] T021 [P] [US2] Тест `GET /health` в `daily-ml-emulator/tests/test_api.py`: код 200 и тело `{"status": "ok"}` (FR-015)

### Implementation for User Story 2

- [X] T022 [US2] Реализовать эндпоинт `GET /health` в `daily-ml-emulator/src/daily_ml_emulator/app.py`
- [X] T023 [US2] Создать `daily-ml-emulator/Dockerfile` по образцу `backend/Dockerfile`: multi-stage `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` → `python:3.12-slim-bookworm`, непривилегированный пользователь, `EXPOSE 8000`, копирование `src/` и `universe/`, запуск uvicorn на порту 8000
- [X] T024 [US2] Добавить сервис `daily-ml-emulator` в `deployments/docker-compose/docker-compose.yml`: `build.context: ../../daily-ml-emulator`, порт `${DAILY_ML_EMULATOR_PORT:-8100}:8000`, healthcheck по `/health`. Без `depends_on`, без `DATABASE_URL` и без `TBANK_INVEST_READ_TOKEN` — эмулятор ни с чем не связывается (FR-018, research.md §6)
- [X] T025 [P] [US2] Добавить `DAILY_ML_EMULATOR_PORT=8100` с поясняющим комментарием в `deployments/docker-compose/.env.example`. Секретов среди переменных эмулятора нет
- [X] T026 [US2] Добавить в `scripts/check.sh` четыре шага эмулятора рядом с шагами backend: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` — каждый через `uv run` с рабочим каталогом `daily-ml-emulator`. Сборка образа отдельного шага не требует: `docker compose build` собирает все сервисы файла (SC-007)

**Checkpoint**: User Stories 1 и 2 работают независимо; гейт проекта покрывает эмулятор

---

## Phase 5: User Story 3 - Описание, инструкция и документация эндпоинтов (Priority: P2)

**Goal**: человек, не участвовавший в разработке, понимает назначение и границы эмулятора,
запускает его по инструкции и сверяет ответ с описанным контрактом, не открывая код

**Independent Test**: следуя только документации, собрать образ, запустить контейнер и
получить первое ранжирование, ни разу не заглянув в исходный код

- [X] T027 [P] [US3] Написать `daily-ml-emulator/README.md` (FR-019): назначение, что эмулятор изображает, раздел «что он сознательно не делает» из раздела «Вне объёма» spec.md, условие замены настоящей моделью, ссылка на `MR-MASTER-DRO` как на источник истины о модели и ссылки на contracts/ и quickstart.md
- [X] T028 [US3] Задать метаданные OpenAPI в `daily-ml-emulator/src/daily_ml_emulator/app.py` (FR-022): `title`, `description` с предупреждением об эмуляции, `summary` и описания полей ответа, чтобы `/docs` и `/openapi.json` были самодостаточны
- [X] T029 [US3] Сверить `specs/002-daily-ml-emulator/quickstart.md` с фактическим поведением реализации (FR-020): выполнить все команды сценариев С1–С8 дословно и привести документ в соответствие, если что-то разошлось
- [X] T030 [US3] Сверить `specs/002-daily-ml-emulator/contracts/daily-ml-emulator-api.md` с фактическим ответом (FR-021, FR-023, SC-009, SC-010): недокументированных полей ответа 0, описанных, но отсутствующих полей 0, у каждого эндпоинта есть примеры запроса и ответа, разделение «переживёт замену / исчезнет» соответствует реализации. При расхождении сначала правится артефакт (Принцип III)

**Checkpoint**: эмулятор можно освоить и запустить по документации, не читая код

---

## Phase 6: User Story 4 - Настройка вселенной активов (Priority: P3)

**Goal**: список активов задаётся без пересборки образа, а заведомо непригодная
конфигурация не даёт контейнеру подняться

**Independent Test**: запустить контейнер со смонтированным своим файлом вселенной из двух
активов и убедиться, что ранжирование содержит ровно их

### Tests for User Story 4

- [X] T031 [P] [US4] Тесты валидации вселенной в `daily-ml-emulator/tests/test_universe.py`: пустой список, дубликат `asset_id`, дубликат `price_series_id`, пустые значения полей, более 200 записей, нечитаемый JSON и отсутствующий файл — каждый случай приводит к отказу с сообщением, называющим причину (FR-017, data-model.md §1)
- [X] T032 [P] [US4] Тест применения конфигурации в `daily-ml-emulator/tests/test_universe.py`: при вселенной из двух активов ответ содержит ровно эти два актива с рангами 1 и 2 (FR-016, FR-002)

### Implementation for User Story 4

- [X] T033 [US4] Реализовать строгую валидацию вселенной при старте в `daily-ml-emulator/src/daily_ml_emulator/universe.py` по всем правилам data-model.md §1, с сообщением, называющим конкретное правило и конкретную запись
- [X] T034 [US4] Обеспечить отказ старта при непригодной конфигурации: процесс завершается с ненулевым кодом, а не поднимается и отвечает 500 (FR-017, research.md §8) — в `daily-ml-emulator/src/daily_ml_emulator/app.py`
- [X] T035 [US4] Проверить переопределение пути вселенной через `DAILY_ML_EMULATOR_UNIVERSE_PATH` и монтирование файла томом по сценарию С6 из quickstart.md, без пересборки образа (FR-016)

**Checkpoint**: все четыре user story работают независимо

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: проектная документация и финальная проверка готовности

- [X] T036 [P] Актуализировать `README.md` (FR-024, Принцип IX): эмулятор в структуре репозитория и в составе стенда, переменная `DAILY_ML_EMULATOR_PORT` в таблице конфигурации, команда запуска. Контейнер `Daily ML` целевой диаграммы НЕ описывается как реализованный — эмулятор указывается как временный стенд-заместитель, которому на смену придёт настоящая модель
- [X] T037 [P] Актуализировать `AGENTS.md` (FR-024, FR-025): эмулятор в перечне компонентов с той же оговоркой, и отдельно — что источником истины о модели ранжирования является исследовательский репозиторий `MR-MASTER-DRO`, подключаемый локально как `Daily ML/` и не входящий в Git этого проекта
- [X] T038 Прогнать `scripts/check.sh` целиком, без `--no-docker`: линтеры, форматирование, типы и тесты эмулятора проходят, сборка образов проходит (Принцип IV, SC-007). Частичные прогоны полным гейтом не считаются
- [X] T039 Пройти сценарии С1–С8 из `specs/002-daily-ml-emulator/quickstart.md` на запущенном стенде и зафиксировать, что критерии SC-001…SC-010 выполнены
- [X] T040 Проставить `Status: Delivered` в `specs/002-daily-ml-emulator/spec.md` и отметить пункты в `specs/002-daily-ml-emulator/checklists/requirements.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: зависимостей нет, начинается сразу
- **Foundational (Phase 2)**: после Setup. **Блокирует все user story**
- **User Story 1 (Phase 3)**: после Foundational. Ни от каких других историй не зависит
- **User Story 2 (Phase 4)**: после Foundational. Технически независима от US1, но проверять контейнер осмысленно, когда есть что отвечать
- **User Story 3 (Phase 5)**: T027 после Foundational; T028 после US1; T029 и T030 — после US1 и US2, поскольку сверяются с фактическим поведением запущенного контейнера
- **User Story 4 (Phase 6)**: после Foundational. Расширяет `universe.py` из T008
- **Polish (Phase 7)**: после всех желаемых историй. T038 и T039 — последними

### User Story Dependencies

- **US1 (P1)**: независима — это MVP
- **US2 (P2)**: независима от US1 по коду; общий файл `app.py` требует последовательности с T019, если работает один человек
- **US3 (P2)**: T029 и T030 сверяются с реальным поведением, поэтому выполняются после US1 и US2
- **US4 (P3)**: независима; трогает `universe.py` и `app.py`

### Within Each User Story

- Тесты пишутся первыми и падают до реализации
- Правило ранжирования (T016, T017) — до эндпоинта (T019)
- Схемы ответа (T018) — до эндпоинта (T019)
- Обработчик ошибок (T010) — до приведения кода ошибки (T020)

### Parallel Opportunities

- **Phase 1**: T004 параллельна T002 и T003 (разные файлы). T005 — после T002 и T003
- **Phase 2**: T006 и T007 параллельны (разные файлы). T008 после T007, T009 после T006 и T008, T010 после T009
- **Phase 3**: все пять тестовых задач T011–T015 параллельны между собой. T018 параллельна T016 и T017 (разные файлы)
- **Phase 4**: T021 и T025 параллельны остальным
- **Phase 5**: T027 параллельна остальным задачам фазы
- **Phase 6**: T031 и T032 параллельны между собой
- **Phase 7**: T036 и T037 параллельны (разные файлы)

Ограничение по общим файлам: T009, T010, T018, T019, T020, T022, T028, T034 правят один и
тот же `app.py` — параллельно между собой не выполняются.

---

## Parallel Example: User Story 1

```bash
# Тесты User Story 1 пишутся вместе — разные проверки, до реализации:
Task: "Тесты правила ранжирования в daily-ml-emulator/tests/test_ranking.py"
Task: "Тесты детерминизма в daily-ml-emulator/tests/test_ranking.py"
Task: "Контрактные тесты GET /rankings в daily-ml-emulator/tests/test_api.py"
Task: "Тесты признака эмуляции в daily-ml-emulator/tests/test_api.py"
Task: "Тесты ошибок в daily-ml-emulator/tests/test_api.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup
2. Phase 2: Foundational — блокирует всё остальное
3. Phase 3: User Story 1
4. **ОСТАНОВИТЬСЯ И ПРОВЕРИТЬ**: `uv run uvicorn daily_ml_emulator.app:app --port 8100` и запрос ранжирования отвечают по контракту
5. На этом шаге фича уже даёт ценность: контракт звена ранжирования зафиксирован и работает

### Incremental Delivery

1. Setup + Foundational → основание готово
2. + US1 → работает ранжирование (**MVP**)
3. + US2 → поднимается контейнером, входит в гейт проекта
4. + US3 → осваивается и запускается по документации
5. + US4 → вселенная настраивается, непригодная конфигурация не поднимается
6. + Polish → проектная документация актуальна, полный гейт пройден

### Parallel Team Strategy

Фича небольшая, и её выгоднее вести одним человеком: US1, US2 и часть US3 правят общий
`app.py`. Если работают двое, разумное разделение после Foundational — US1 и US4 (разные
файлы: `ranking.py` против `universe.py`), с последующим объединением на US2 и US3.

---

## Notes

- `[P]` — разные файлы, нет незакрытых зависимостей
- Метка `[Story]` связывает задачу с user story для прослеживаемости
- Тесты должны падать до реализации
- Коммит после каждой задачи или логической группы
- На любом checkpoint можно остановиться и проверить историю независимо
- При расхождении реализации со спецификацией первым правится артефакт (Принцип III), а не код
- `float` на пути формирования `score` запрещён, как и на денежном пути проекта

---

## Phase 8: Convergence

Добавлено командой `/speckit-converge` 2026-08-29. Все 25 функциональных требований
выполнены, нарушений конституции нет; ниже — частичные расхождения между артефактами и
фактическим состоянием.

- [X] T041 Привести SC-007 в соответствие с фактом: устранить расхождение переводов строк во `frontend/` (40 файлов, шаг `frontend: prettier` в `scripts/check.sh`) либо, если правка frontend вне объёма фичи, зафиксировать отступление в `specs/002-daily-ml-emulator/spec.md` рядом с SC-007 per SC-007 (partial)
- [X] T042 Отразить в `specs/002-daily-ml-emulator/plan.md` (раздел Project Structure) изменение структуры репозитория: добавлен каталог `docs/` с `architecture.md` и `daily-ml-model.md`, каталог `source/` удалён, диаграмма перенесена в `docs/container-diagram.svg` per plan: Project Structure, Constitution V (partial)
- [X] T043 Описать в `specs/002-daily-ml-emulator/contracts/daily-ml-emulator-api.md` общую форму ошибок для кодов `404` и `405` — `{"detail": {"code": "error", "message": "..."}}`, возвращаемую обработчиком `StarletteHTTPException` per FR-021, SC-010 (partial)
- [X] T044 Добавить в `daily-ml-emulator/tests/test_api.py` тест переопределения `DAILY_ML_EMULATOR_MODEL_ID`: текущий тест проверяет только значение по умолчанию, а FR-016 требует настраиваемости идентификатора модели per FR-016, Constitution IV (partial)
