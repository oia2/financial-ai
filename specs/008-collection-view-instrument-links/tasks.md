---

description: "Task list for feature implementation"
---

# Tasks: Экран сбора и связи инструментов

**Input**: Design documents from `/specs/008-collection-view-instrument-links/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: испытания включены — спецификация требует их прямо (FR-029: каждый случай
изменения состава инструментов покрывается испытанием).

**Organization**: задачи сгруппированы по историям спецификации, чтобы каждую можно было
довести и проверить отдельно.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно делать параллельно (разные файлы, нет незакрытых зависимостей)
- **[Story]**: к какой истории относится задача (US1, US2, US3)

## Path Conventions

Веб-приложение: `backend/src/financial_ai/`, `backend/tests/`, `frontend/src/`.
Макет — в проекте Open Design «Портфель FINANCIAL AI», файл `market-data.html`.

---

## Phase 1: Setup

**Purpose**: выяснить то, что нельзя угадывать, и подготовить почву для испытаний.

- [X] T001 Живая сверка источника: есть ли в описании фьючерсного контракта идентификатор базовой бумаги и совпадает ли он с идентификатором бумаги на доске акций. Команда по образцу существующих `verify-*` в `backend/src/financial_ai/market_data/cli.py`; результат записать в `backend/src/financial_ai/market_data/PROVENANCE.md` рядом со сверкой 2026-09-04
- [X] T002 [P] Убрать временное `DAILY_ML_REQUIRED_DATA_GROUPS=["quotes"]` из `deployments/docker-compose/.env` и описать в `deployments/docker-compose/docker-compose.yml`, что настройка влияет только на запуск ранжирования
- [X] T003 [P] Завести фикстуры записанных ответов источника для испытаний состава инструментов в `backend/tests/fixtures/instruments/` (серии срочного рынка, открытый интерес, справочник бумаг)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: правила и хранилище, на которые опираются все истории.

**⚠️ Ни одна история не начинается, пока эта фаза не закрыта.**

- [X] T004 Миграция `backend/migrations/versions/0011_instrument_links.py`: таблицы `market_asset_futures_link`, `market_asset_alias`, `market_session_skip`; колонки `market_asset.isin`, `market_futures_position.contract_code` (в первичный ключ), `market_ingest_run.period_from` и `period_till`
- [X] T005 [P] Модели `AssetFuturesLink`, `AssetAlias`, `SessionSkip` и новые колонки в `backend/src/financial_ai/market_data/models.py` по [data-model.md](./data-model.md)
- [X] T006 [P] Объявление торгового календаря в `backend/src/financial_ai/market_data/calendar_spec.py`: идентификатор, рынок, доска, опорная бумага, пояс и правила даты решения (`t` после закрытия, исполнение на открытии `t+1`, в тот же день нельзя, лаг одна сессия) — перенос из `configs/calendars/moex_daily.yaml` исследовательского репозитория (FR-042)
- [X] T007 Чтение и запись связей, псевдонимов, пропусков и периода исхода в `backend/src/financial_ai/market_data/repository.py`
- [X] T008 Одно правило полноты в `backend/src/financial_ai/market_data/completeness.py`: сессия группы закрыта, когда по каждому источнику есть непустое наблюдение либо успешный исход, период которого включает сессию (FR-032, FR-033)
- [X] T009 Поиск пропусков по всем группам с осью сессий независимо от перечня групп, обязательных для модели, в `backend/src/financial_ai/market_data/advance.py` (FR-031)
- [X] T010 [P] Модульные тесты правила полноты и диапазонных исходов в `backend/tests/unit/test_completeness_rule.py`

**Checkpoint**: правила и хранилище готовы, истории можно вести параллельно.

---

## Phase 3: User Story 1 — Видно, что делает сбор и почему чего-то нет (P1) 🎯 MVP

**Goal**: человек отвечает на вопросы «идёт ли сбор, что именно сейчас, почему сессия не собрана, когда следующий сбор» без чтения журналов сервера.

**Independent Test**: запустить сбор с заведомо недоступным источником; экран называет идущий и следующий источник, причину каждой несобранной сессии и сохраняет итог после завершения прогона и после перезапуска сборщика.

### Tests for User Story 1

- [X] T011 [P] [US1] Контрактный тест состояния прогона (режим, план источников, исходы сессий, причины пропусков) в `backend/tests/contract/test_collection_state_api.py` по [contracts/collection-state-api.md](./contracts/collection-state-api.md)
- [X] T012 [P] [US1] Контрактный тест журнала прогонов в `backend/tests/contract/test_runs_api.py`
- [X] T013 [P] [US1] Интеграционный тест: причины пропусков и итог прогона доступны после перезапуска сборщика в `backend/tests/integration/test_run_journal.py`
- [X] T014 [P] [US1] Интеграционный тест: сессия, закрывшаяся сегодня, берётся в сбор в тот же вечер в `backend/tests/integration/test_calendar_timing.py` (SC-010)

### Implementation for User Story 1

- [X] T015 [US1] Запись пропусков с причиной из четырёх мест решения в `backend/src/financial_ai/market_data/advance.py` и `ingest.py`: отложено до закрытия, выдержка после неудачи, исчерпан предел попыток, разрыв больше предела (FR-002)
- [X] T016 [US1] План источников текущей сессии с порядком, состоянием и областью (`session`, `period`, `daily`) в состоянии прогона в `backend/src/financial_ai/market_data/runner.py` (FR-003, FR-007)
- [X] T017 [US1] Режим прогона (`daily`, `manual`) и исходы сессий в состоянии прогона в `backend/src/financial_ai/market_data/runner.py` и `scheduler.py` (FR-001, FR-004)
- [X] T018 [US1] Чтение итога прогона и последних прогонов из таблицы исходов в новом `backend/src/financial_ai/market_data/journal.py` (FR-005, FR-006)
- [X] T019 [US1] Исход «прерван» для прогона, не завершённого из-за перезапуска, при старте сборщика в `backend/src/financial_ai/market_data/scheduler.py` (FR-041)
- [X] T020 [US1] Опрос календаря после порога и равнение сбора на последнюю дату календаря в `backend/src/financial_ai/market_data/advance.py`; убрать безусловный опрос календаря внутри `ingest.ingest_session` (FR-040, FR-043)
- [X] T021 [US1] Убрать дивиденды из ежедневного плана в `backend/src/financial_ai/market_data/ingest.py`, сохранив собранные события (FR-008)
- [X] T022 [US1] Внутренний маршрут журнала прогонов в `backend/src/financial_ai/worker/routes/` и публичный `GET /api/market-data/runs` в `backend/src/financial_ai/api/routes/market_data.py`
- [X] T023 [P] [US1] Типы состояния прогона и журнала в `frontend/src/entities/market-data/types.ts` и запросы в `api.ts`
- [X] T024 [US1] Панель прогона по макету в `frontend/src/widgets/catchup-section/`: план источников с идущим и следующим, причины пропусков, итог и журнал, состояния остановки и недоступности сборщика
- [X] T025 [P] [US1] Виджет календаря сессий в `frontend/src/widgets/collection-calendar/`: факт слева от сегодня, ожидание пунктиром, листание по месяцам (FR-023, FR-024)
- [X] T026 [US1] Строка расписания в `frontend/src/pages/market-data/MarketDataPage.tsx`: следующий сбор датой сессии из календаря, пауза автосбора влияет на расписание (FR-024a, FR-025)
- [X] T027 [P] [US1] Тесты интерфейса состояний прогона в `frontend/src/widgets/catchup-section/__tests__/` по [contracts/ui-states.md](./contracts/ui-states.md)

**Checkpoint**: раздел рассказывает правду о сборе; истории US2 и US3 можно вести дальше.

---

## Phase 4: User Story 2 — Видно, по каким бумагам данные вообще бывают (P2)

**Goal**: неполнота группы позиций объяснена числами, а не догадкой.

**Independent Test**: открыть сведения группы позиций и убедиться, что число бумаг и число бумаг с фьючерсом приходят с сервера на дату сводки и совпадают с хранилищем, а открытие раздела не порождает обращений к бирже.

### Tests for User Story 2

- [X] T028 [P] [US2] Контрактный тест сводки с составом бумаг в `backend/tests/contract/test_coverage_api.py` по [contracts/coverage-api.md](./contracts/coverage-api.md)
- [X] T029 [P] [US2] Интеграционный тест: открытие сводки не обращается к внешним источникам в `backend/tests/integration/test_coverage_offline.py` (SC-006)

### Implementation for User Story 2

- [X] T030 [P] [US2] Сбор ISIN в справочник бумаг в `backend/src/financial_ai/market_data/sources/equity_d1.py` и `securities.py` (FR-018)
- [X] T031 [US2] Число бумаг и число бумаг с фьючерсом на дату сводки в `backend/src/financial_ai/market_data/coverage.py`; знаменатель — бумаги с котировкой в успешно собранной сессии (FR-010, FR-013, FR-037)
- [X] T032 [US2] Исход каждого источника группы в сводке в `backend/src/financial_ai/market_data/coverage.py` (FR-032)
- [X] T033 [US2] Строка группы и раскрытие с исходами источников в `frontend/src/widgets/completeness-table/`

**Checkpoint**: сводка объясняет свои числа.

---

## Phase 5: User Story 3 — Состав инструментов меняется, и это видно (P2)

**Goal**: появление, исчезновение и переименование инструментов не ломают сбор и не теряют данные молча.

**Independent Test**: прогнать сбор на подготовленных ответах источника, в которых состав меняется от прогона к прогону; прогон не прерывается, ряды не смешиваются, каждое изменение видно в отчёте.

### Tests for User Story 3

- [X] T034 [P] [US3] Испытание: появилась новая бумага — попадает в состав и в знаменатель, в `backend/tests/integration/test_instruments_new_asset.py`
- [X] T035 [P] [US3] Испытание: у бумаги впервые появился фьючерс — связь открывается с подтверждённой даты, ранние даты ежедневным сбором не запрашиваются, в `backend/tests/integration/test_instruments_new_future.py`
- [X] T036 [P] [US3] Испытание: сменилось семейство контрактов — прежний интервал закрыт, новый открыт, смена видна как событие, в `backend/tests/integration/test_instruments_contract_change.py`
- [X] T037 [P] [US3] Испытание: бумага переименована — связь и история не рвутся, наблюдения относятся к прежней сущности, в `backend/tests/integration/test_instruments_rename.py`
- [X] T038 [P] [US3] Испытание: бумага перестала торговаться — выпадает из знаменателя тех сессий, где нет котировки, позиции за них не запрашиваются, в `backend/tests/integration/test_instruments_delisted.py`
- [X] T039 [P] [US3] Испытание: у бумаги несколько контрактов — выбор однозначен, повторяем и сохранён с основанием, в `backend/tests/integration/test_instruments_multiple_contracts.py`
- [X] T040 [P] [US3] Испытание: бумага, по которой позиции собирались, перестала сопоставляться — неуспех источника с причиной, а не «фьючерса нет», в `backend/tests/integration/test_instruments_link_lost.py` (FR-020a)

### Implementation for User Story 3

- [X] T041 [US3] Ведение связей во времени в новом `backend/src/financial_ai/market_data/links.py`: открытие, продление, закрытие интервала, основание выбора, событие смены (FR-014, FR-015, FR-016)
- [X] T042 [US3] Связь по идентификатору базовой бумаги либо по тикеру с якорем ISIN — по итогу сверки T001 — в `backend/src/financial_ai/market_data/sources/positions_client.py` (FR-020b, FR-020c)
- [X] T043 [US3] Применимость позиций по действующей связи и запись контракта в наблюдение в `backend/src/financial_ai/market_data/sources/positions.py` (FR-017, FR-039, FR-020d)
- [X] T044 [US3] Переименование бумаги: ведение псевдонимов с датами и отнесение новых наблюдений к прежней сущности в `backend/src/financial_ai/market_data/links.py` и `sources/equity_d1.py` (FR-038)
- [X] T045 [US3] Потеря соответствия у бумаги с историей позиций — неуспех источника с причиной в `backend/src/financial_ai/market_data/sources/positions.py` (FR-020a)
- [X] T046 [US3] События связей в журнале прогонов и в ответе состояния в `backend/src/financial_ai/market_data/journal.py`

**Checkpoint**: изменение состава инструментов перестаёт быть тихим.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T047 [P] Обновить `README.md` и `AGENTS.md`: состав источников без дивидендов, объявление календаря, новые таблицы
- [X] T048 [P] Обновить `backend/src/financial_ai/market_data/PROVENANCE.md`: что перенесено из исследовательского репозитория в этой фиче и что расходится сознательно
- [X] T049 Сверить раздел с макетом: запустить `verify.cjs` и `check-width.cjs` из `design-assets/market-data-collection-v4/` проекта Open Design и сопоставить состояния с [contracts/ui-states.md](./contracts/ui-states.md)
- [X] T050 Пройти сценарии [quickstart.md](./quickstart.md) на живом стенде, включая проверку 1 (сбор не зависит от настроек модели) и проверку 2 (сессия собирается в тот же вечер)
- [X] T051 Полный гейт `scripts/check.sh` целиком, без `--no-docker`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: начинается сразу. T001 определяет способ связи и потому блокирует T042.
- **Foundational (Phase 2)**: зависит от Setup, блокирует все истории.
- **US1 (Phase 3)**: после Foundational. Ничего не ждёт от US2 и US3.
- **US2 (Phase 4)**: после Foundational. Отдельно от US1, но на экране опирается на перенос панели.
- **US3 (Phase 5)**: после Foundational. T042 ждёт итога сверки T001.
- **Polish (Phase 6)**: после всех историй, которые решено довести.

### Внутри историй

- Испытания пишутся раньше кода и должны падать до его появления.
- Модели → репозиторий → правила → маршруты → интерфейс.
- T016 и T017 трогают один файл (`runner.py`) — параллельно не идут.
- T031 и T032 трогают один файл (`coverage.py`) — параллельно не идут.

### Parallel Opportunities

- Setup: T002 и T003 параллельны.
- Foundational: T005, T006 и T010 параллельны после T004.
- US1: T011–T014 параллельны; T023, T025 и T027 параллельны.
- US3: все семь испытаний T034–T040 параллельны.
- Polish: T047 и T048 параллельны.

---

## Parallel Example: User Story 3

```bash
# Семь испытаний состава инструментов пишутся одновременно — разные файлы:
Task: "Испытание: появилась новая бумага"
Task: "Испытание: у бумаги впервые появился фьючерс"
Task: "Испытание: сменилось семейство контрактов"
Task: "Испытание: бумага переименована"
Task: "Испытание: бумага перестала торговаться"
Task: "Испытание: у бумаги несколько контрактов"
Task: "Испытание: соответствие потеряно"
```

---

## Implementation Strategy

### MVP — только US1

1. Setup (T001–T003).
2. Foundational (T004–T010) — без неё ни одна история не работает.
3. US1 (T011–T027).
4. **Остановиться и проверить**: раздел объясняет, что делает сбор и почему чего-то нет.

Это уже закрывает исходную жалобу: автосбор перестаёт зависеть от настроек модели (T009),
сегодняшняя сессия собирается в тот же вечер (T020), а подвисания становятся видимыми
(T016, T018).

### Дальше по приросту

1. US2 — сводка объясняет свои числа.
2. US3 — состав инструментов меняется без тихих потерь.
3. Polish — документация, сверка с макетом, полный гейт.

---

## Notes

- Задачи `[P]` — разные файлы и никаких незакрытых зависимостей.
- Коммит после каждой задачи или логической группы.
- Гейт готовности — `scripts/check.sh` целиком; частичные прогоны в этом проекте уже
  трижды пропускали дефекты.
- Живые сверки с биржей в гейт не входят: сеть в среде сборки недоступна.

---

## Phase 7: Convergence

Найдено сверкой кода со спецификацией после `/speckit-implement`. Нумерация продолжает
существующую; прежние задачи не меняются.

- [X] T052 Отдавать причины пропусков из `market_session_skip` в состоянии прогона и журнале: `MarketDataRepository.recent_skips` не вызывается ниоткуда, и после перезапуска сборщика причина исчезает, оставляя только счётчик, per FR-002, SC-002 (partial)
- [X] T053 Синхронизировать связи инструментов один раз на прогон, а не на каждую сессию, в `backend/src/financial_ai/market_data/ingest.py`: `links.sync_links` внутри `_sync_positions` стоит трёх обращений к ISS на сессию, а его ответы описывают сегодняшнее состояние, а не состояние собираемой сессии, per AGENTS.md «аккуратность обращений», SC-005 (contradicts)
- [X] T054 Считать состав бумаг по последней УСПЕШНО собранной сессии в `backend/src/financial_ai/market_data/coverage.py` либо называть несобранность состоянием: сейчас несобранная сессия даёт «0 бумаг» и выглядит отсутствием торгов, per FR-019a, FR-013 (partial)
- [X] T055 Возвращать в `next_session` дату ближайшей НЕСОБРАННОЙ сессии, а не последнюю сессию календаря, в `backend/src/financial_ai/market_data/coverage.py`: при отставании раздел обещает сегодняшнюю дату, тогда как сбор возьмёт старую, per FR-024a (partial)
- [X] T056 Свести выбор семейства контрактов к одной реализации: `positions_client.build_contract_map` и `links.build_candidates` считают одно и то же правило по открытому интересу, per Constitution II (unrequested)
