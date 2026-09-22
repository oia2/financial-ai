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

---

## Phase 8: Convergence

Вторая сверка. Пять находок первой закрыты; эти две — её собственный след:
разрыв закрыт на сервере и не доведён до интерфейса, прежний читатель таблицы
оставлен на месте.

- [X] T057 Показывать «состав не посчитан» вместо «0 из 0 бумаг» и называть дату состава, когда она старше даты сводки, в `frontend/src/widgets/completeness-table/GroupsSection.tsx`: сервер различает неизвестный состав (`universe.asof_date: null`) и настоящий ноль, интерфейс это различие теряет, per FR-019a, FR-010, contracts/coverage-api.md (partial)
- [X] T058 Оставить одно чтение таблицы пропусков: `MarketDataRepository.recent_skips` не вызывается ниоткуда с тех пор, как появился `journal.recent_skips`, per Constitution II (unrequested)

---

## Phase 9: Convergence

Третья сверка. Находки Phase 8 закрыты. Первая из этих двух — регрессия,
внесённая T053 и T056 вместе: связи стали открываться раз на прогон, а запасное
соответствие было удалено, и случай «сначала ежедневный прогон, потом догон
истории» перестал работать.

- [X] T059 Дать ручному сбору истории собирать позиции за даты раньше самой ранней связи в `backend/src/financial_ai/market_data/links.py` и `sources/positions.py`: `open_link` сравнивает с интервалом без даты окончания и при том же контракте не сдвигает `valid_from` назад, а `sync_positions` роняет весь источник на пустом наборе связей, per FR-035, FR-017 (contradicts)
- [X] T060 Удалить `plan.session_sources` в `backend/src/financial_ai/market_data/plan.py`: счётчик посессионных источников оказался на фронтенде, и функция не вызывается ниоткуда, per Constitution II (unrequested)

---

## Phase 10: Convergence

Четвёртая сверка. Находки Phase 9 закрыты, находок уровня HIGH нет впервые за
четыре прохода. Первая из этих двух — край, который открыла T059: подстановка
самого раннего интервала не различает настоящее семейство и заглушку.

- [X] T061 Не подставлять заглушку `unknown` как семейство контрактов для дат раньше первого интервала в `backend/src/financial_ai/market_data/repository.py` (`earliest_links_after`): её пишет `_explain_orphans` для бумаг, у которых контракта больше нет, и ручной сбор спросил бы биржу кодом, которого не существует, per FR-022, AGENTS.md «аккуратность обращений» (contradicts)
- [X] T062 Удалить `ratioWidth` в `frontend/src/shared/lib/market-format.ts`: его единственным потребителем были шкалы таблицы полноты, заменённой раскрытием групп по макету v4, per Constitution II (unrequested)

---

## Phase 11: Convergence

Пятая сверка. Находки Phase 10 закрыты; расхождений уровня CRITICAL, HIGH и
MEDIUM нет. Формы всех четырёх ответов сверены с контрактами по составу полей,
мёртвый код проверен сплошь по обе стороны — осталась одна обёртка.

- [X] T063 Удалить `completeness.incomplete_groups` в `backend/src/financial_ai/market_data/completeness.py`: обёртка над `missing_sessions` не вызывается ниоткуда, все потребители зовут `missing_sessions` по группе напрямую, per Constitution II (unrequested)

---

## Phase 12: Композиция страницы и потеря связи

Найдено владельцем проекта на живом стенде 2026-09-18: панель сбора внизу,
кнопка запуска не на месте, при потере сборщика часть раздела выглядит идущей.
Проверки этого поймать не могли — `verify.cjs` сверяет артефакт, а не React.

- [X] T064 Перенести композицию страницы из артефакта в `frontend/src/pages/market-data/MarketDataPage.tsx`: панель сбора идёт сразу после шапки, перед группами, per FR-021 (partial)
- [X] T065 Убрать запуск сбора из шапки: в макете действия не дублируются — пауза только в шапке, запуск только в панели, per FR-021, verify.cjs «действия не дублируются» (contradicts)
- [X] T066 Сделать недоступность сборщика одним состоянием раздела в `MarketDataPage.tsx` и `widgets/catchup-section/CatchupSection.tsx`: уведомление идёт перед панелью, панель не выглядит идущей, команды не предлагаются, per contracts/ui-states.md «вида, будто сбор идёт» (contradicts)

## Phase 13: Ошибка источника и добор макета

Запрошено владельцем проекта 2026-09-18 после сверки страницы с артефактом.

- [X] T067 Показать неудачи источника по дням в макете Open Design (`v4.js`, `v4.css`): дата и причина в раскрытии группы, per FR-021
- [X] T068 Отдавать неудачи источника по дням в сводке в `backend/src/financial_ai/market_data/coverage.py`, per FR-032, contracts/coverage-api.md (missing)
- [X] T069 Перенести список неудач в `frontend/src/widgets/completeness-table/GroupsSection.tsx` и `app/styles/design.css`, per FR-021 (missing)
- [X] T070 Добрать расхождения сверки страницы: строка часового пояса `tz-line`, «Ошибка источника» в легенде календаря, опорная бумага в пояснении, текст про порог — per FR-021, FR-022 (partial)
- [X] T071 Привести состояние «сборщик недоступен» к макету: панель заменяется одной строкой, сводка ниже остаётся последним известным состоянием, per FR-021, contracts/ui-states.md (contradicts)
- [X] T072 Добрать итог прогона по макету: длительность у законченного, «N с назад» и «Идёт» у идущего, per FR-021 (partial)

## Phase 14: Шкала на длинном прогоне и рост псевдонимов

Найдено владельцем проекта на живом стенде 2026-09-18.

- [X] T073 Сделать дорожку сплошной на длинном прогоне — в макете Open Design (`v4.css`, `v4.js`) и в `frontend/src/widgets/catchup-section/SessionProgress.tsx`: при 216 сессиях 215 зазоров по 3px дают 645 пикселей там, где дорожке отведено около 400, и шкалы не видно, per FR-021 (contradicts)
- [X] T074 Писать псевдоним при СМЕНЕ имени, а не в каждом прогоне, в `backend/src/financial_ai/market_data/links.py`: на стенде накопилось 23 276 строк на 506 бумаг, per FR-038, Constitution II (contradicts)
- [X] T075 Свернуть накопленные дубликаты псевдонимов миграцией `backend/migrations/versions/0012_collapse_alias_duplicates.py`, per FR-038 (partial)

## Phase 15: Журнал событий, шкала источников, сведения о дате

Запрошено владельцем проекта 2026-09-18.

- [X] T076 Три состояния источника вместо двух в `backend/src/financial_ai/market_data/coverage.py`: `partial` — неполнота без неудач, `failed` — только с записанными неудачами; экран называл ошибкой всякую неполноту, per FR-032 (contradicts)
- [X] T077 Журнал событий идущего прогона: `log` в состоянии прогона (`runner.py`) и виджет `frontend/src/widgets/catchup-section/EventLog.tsx`, per FR-021 (missing)
- [X] T078 Шкала по источникам для прогона из одной сессии в `frontend/src/widgets/catchup-section/SessionProgress.tsx`, per FR-021, FR-026 (missing)
- [X] T079 Сведения о дате и сбор одной сессии в `frontend/src/widgets/collection-calendar/CollectionCalendar.tsx`: клетка календаря открывает состав дня, причину пропуска и запуск ручного сбора диапазоном в один день, per FR-021 (missing)
- [X] T080 Потолок у всех журналов: `skips_total`, `failures_total`, `LOG_KEPT`, и подпись «показаны последние N» — раскрытый список не должен оказаться бесконечным, per Constitution II (partial)

## Phase 16: План ручного сбора, слипшаяся строка, выбор после остановки

Найдено владельцем проекта на живом стенде 2026-09-18.

- [X] T081 Считать план ручного сбора по ВСЕМ группам, а не по котировкам, в `backend/src/financial_ai/market_data/runner.py` и `completeness.py`: на стенде догон запросил четыре сессии там, где недобранных были сотни — это исходная жалоба фичи, закрытая для автосбора и оставшаяся в ручном, per FR-031, FR-032 (contradicts)
- [X] T082 Разделить строку «Следующий сбор» в `frontend/src/widgets/collection-calendar/CollectionCalendar.tsx`: дата и порог слипались, а московское время показывается только при несовпадении поясов, per FR-021, FR-022 (contradicts)
- [X] T083 Состояние «остановлен вами» с выбором «продолжить или отменить» — в макете Open Design и в `frontend/src/widgets/catchup-section/CatchupSection.tsx`: остановка не отмена, непройденные сессии никуда не делись, per FR-021 (missing)

## Phase 17: Быстрая остановка, границы календаря, потолки журналов

Найдено владельцем проекта на живом стенде 2026-09-18.

- [X] T084 Проверять остановку между источниками сессии и между инструментами у позиций в `backend/src/financial_ai/market_data/ingest.py` и `sources/positions.py`: прежде признак ждал конца сессии, а сессия с позициями идёт по обращению на каждый из десятков контрактов, per FR-044 (contradicts)
- [X] T085 Не листать календарь назад дальше самой ранней известной сессии: `earliest_month` в `calendar_view.py`, граница в `CollectionCalendar.tsx`, per FR-024 (partial)
- [X] T086 Убрать скачок страницы при листании месяца: постоянная высота сетки в макете и в `design.css`, прошлый месяц остаётся на экране до загрузки следующего, per FR-021 (contradicts)
- [X] T087 Показать остаток у всех журналов: `events_total` и `skips_total` в ответе журнала и подписи «показаны последние N из M», per Constitution II (partial)

## Phase 18: Календарь, расписание, слипшийся порог

- [X] T088 Вернуть высоту клеток календаря: ряды растут по содержимому, а шесть рядов набираются пустыми клетками — зажатая в 50px клетка роняла наружу подпись и точки групп, per FR-021 (contradicts)
- [X] T089 Разделить «Следующий сбор» на момент и взятую сессию — в макете и в `CollectionCalendar.tsx`: при отставании дата из прошлого рядом со словом «следующий» читалась как ошибка, per FR-024a (contradicts)
- [X] T090 Пробел перед московским порогом: «после 23:30 (порог 19:30 МСК)», per FR-022 (contradicts)

## Phase 19: Итог автоматического прогона и граница календаря

- [X] T091 Показывать ПОСЛЕДНИЙ прогон, чей бы он ни был, в `backend/src/financial_ai/worker/routes/catchup.py`: состояние автосбора отдавалось, только пока он идёт, и остановивший его видел пустую панель, будто прогона не было, per FR-025 (contradicts)
- [X] T092 Считать границу листания календаря по наблюдениям, а не по календарю, в `repository.earliest_observed_session` и `calendar_view.py`: календарь знает торги с 2013 года, собранного там нет, per FR-024 (partial)

## Phase 20: Текст остановки и остановка в журнале событий

- [X] T093 Записывать остановку и завершение в журнал событий прогона (`runner.py`, `scheduler.py`) и показывать журнал после прогона: события писались в его конце, а журнал в тот же миг прятался, per FR-021 (missing)
- [X] T094 Убрать из уведомления об остановке отменённое правило «текущая сессия доводится до конца: день, собранный наполовину, неотличим от собранного полностью» — оно противоречит FR-044, per FR-044 (contradicts)

## Phase 21: Остановка в автосборе и согласие чисел группы

- [X] T095 Проверять остановку внутри сессии и в ежедневном сборе: `ingest_session` признака не принимал вовсе, и остановка автосбора ждала всю сессию — включая обращение за позициями по каждому из десятков контрактов, per FR-044 (contradicts)
- [X] T096 Считать покрытие группы тем же правилом, что и исход её источников, в `coverage.py`: строка группы шла от наблюдений, а раскрытие — от исходов прогонов, и рядом стояли «11 из 82» и «13 из 82», per FR-032 (contradicts)
- [X] T097 Испытание против расхождения путей в `backend/tests/integration/test_both_paths_agree.py`: правило меняли в одном пути и забывали про второй трижды, per Constitution IV (missing)

## Phase 22: Дата состава и остаток остановленного прогона

- [X] T098 Объяснить дату состава в заголовке групп — «по последней собранной сессии», в макете и в `GroupsSection.tsx`: одна дата рядом с другой ничего не объясняла, per FR-019a (partial)
- [X] T099 Считать непройденные сессии несобранными в подписи прогона (макет и `CatchupSection.tsx`): остановленный прогон сообщал «все сессии прогона собраны» про то, к чему не приступали, per FR-025 (contradicts)

## Phase 23: Порядок сбора и подпись остатка

- [X] T100 Собирать последнюю закрытую сессию ПЕРВОЙ в `advance.py`: порядок «от старых к новым» при отставании отдавал свежие данные последними, и собранное кончалось 11.09 при календаре до 17.09, per FR-045 (contradicts)
- [X] T101 Не отменять сбор последней закрытой сессии при разрыве сверх предела, per FR-046 (contradicts)
- [X] T102 Различать в подписи прогона «не собрано», «пропущено» и «не начинались» в `CatchupSection.tsx`: раньше три случая складывались в одно число и подпись читалась как «не собрано: 72 (из них 72 не начинались)», per FR-025 (partial)

---

## Phase 24: Convergence

Шестая сверка. Находки Phase 22 и 23 закрыты. Первая из этих двух — след
самой Phase 23: порядок сбора и порядок показа оказались одним значением.

- [X] T103 Оставить порядок показа хронологическим при изменённом порядке сбора: `scheduler.py` кладёт план как пришёл, поэтому `date_from` стал позже `date_till`, а сегменты шкалы идут «сегодня, потом старые» — шкалу читают слева направо как хронологию, per FR-001, FR-021 (contradicts)
- [X] T104 Сделать проверку удержания блокировки детерминированной в `backend/tests/integration/test_sync_dedup.py`: она мигает, а мигающая проверка приучает перезапускать гейт вместо того, чтобы ему верить, per Constitution IV (contradicts)
- [X] T105 Обобщить порядок сбора до «от свежих к старым» в `advance.py`: правило «последняя закрытая первой» не срабатывало, когда её нет в плане — например, когда она ждёт выдержки, — и порядок снова оказывался от самых старых, per FR-045 (partial)
- [X] T106 Добавить повторы источнику ЦБ в `backend/src/financial_ai/market_data/sources/cbr.py`: сайт роняет часть соединений (54 неуспеха на 118 успехов), повторов не было вовсе, и каждый обрыв стоил всей сессии; заодно причина названа, когда обрыв молчит, per Constitution II (missing)

---

## Phase 25: Convergence

Седьмая сверка. Находки Phase 24 закрыты, расхождений выше LOW нет впервые.
Проверено, что изменение порядка сбора не задело тех, кто читает собранное:
`reconcile` и план портфеля берут максимум, а не последний элемент.

- [X] T107 Удалить `frontend/src/widgets/catchup-section/ProgressTrack.tsx`: индикатор хода из фичи 005, чьё место в макете v4 заняла шкала сессий с сегментами и легендой; потребителей нет ни в коде, ни в тестах, per Constitution II (unrequested)

## Phase 26: Внешнее ревью и план прогона

- [X] T108 Считать полноту группы по наблюдениям каждого источника отдельно: разделить ряды `market_global_daily_series` между `global_series`, `cbr`, `brent`, `index_constituents` в backend/src/financial_ai/market_data/{groups.py,completeness.py,repository.py} per FR-047 (contradicts)
- [X] T109 Сверять псевдонимы до записи наблюдений сессии и ключевать агрегаты торгов сущностью в backend/src/financial_ai/market_data/{ingest.py,sources/equity_agg.py} per FR-048 (contradicts)
- [X] T110 Датировать связи прогона догона последней сессией окна в backend/src/financial_ai/market_data/ingest.py per FR-049 (contradicts)
- [X] T111 Записывать прерванный источник исходом «прервано» и не закрывать по нему сессию в backend/src/financial_ai/market_data/{ingest.py,sources/positions.py,completeness.py} per FR-050 (contradicts)
- [X] T112 Различать контракты одной бумаги в слепке позиций в backend/src/financial_ai/ranking/dataset.py per FR-051 (contradicts)
- [X] T113 Держать один идентификатор прогона на весь прогон, включая догон и автосбор, в backend/src/financial_ai/market_data/{models.py,ingest.py,advance.py} и миграции backend/migrations/versions/ per FR-052 (contradicts)
- [X] T114 Спрашивать позиции только у бумаг, торговавшихся в эту сессию, в backend/src/financial_ai/market_data/sources/positions.py per FR-053 (contradicts)
- [X] T115 Называть датой следующего сбора ту сессию, которую сбор возьмёт первой, в backend/src/financial_ai/market_data/coverage.py per FR-054 (contradicts)
- [X] T116 Спрашивать секторы и лоты раз в сутки и пометить их в плане суточными в backend/src/financial_ai/market_data/{plan.py,ingest.py,advance.py} per FR-055 (contradicts)
- [X] T117 Показывать торговый календарь в плане его исходом и помечать «следующим» только посессионный источник в backend/src/financial_ai/market_data/advance.py и frontend/src/widgets/catchup-section/SourceRail.tsx per FR-056 (contradicts)
- [X] T118 Покрыть испытаниями каждое из правил FR-047…FR-056 в backend/tests/market_data/ и frontend/src/widgets/catchup-section/__tests__/

## Phase 27: Продолжение остановленного прогона

- [X] T119 Сохранять состояние диапазонных источников через границу сессии в backend/src/financial_ai/market_data/runner.py per FR-057 (contradicts)
- [X] T120 Продолжать остановленный прогон его непройденными сессиями в backend/src/financial_ai/worker/routes/catchup.py, backend/src/financial_ai/market_data/runner.py и frontend/src/features/catchup-launch/useCatchupControl.ts per FR-058 (contradicts)
- [X] T121 Перечитывать состояние сразу после команды в frontend/src/features/catchup-launch/useCatchupControl.ts per FR-059 (contradicts)
- [X] T122 Покрыть испытаниями FR-057…FR-059 в backend/tests/market_data/ и frontend/tests/

## Phase 28: Сквозные случаи внешнего ревью

- [X] T123 Не закрывать сессию наблюдениями, если последний исход источника — «прервано», в backend/src/financial_ai/market_data/{completeness.py,repository.py} per FR-050 (contradicts)
- [X] T124 Запретить сверку связей задним числом и закрытие интервала раньше его начала в backend/src/financial_ai/market_data/{ingest.py,repository.py} per FR-049 (contradicts)
- [X] T125 Опознавать бумаги по ISIN независимо от выбора источников в backend/src/financial_ai/market_data/ingest.py per FR-048 (contradicts)
- [X] T126 Считать остановленный источник несобранной сессией в журнале прогонов в backend/src/financial_ai/market_data/journal.py per FR-050 (contradicts)
- [X] T127 Заводить исход источника до обращения, а не после в backend/src/financial_ai/market_data/{ingest.py,repository.py} per FR-052 (partial)
- [X] T128 Считать дату следующего сбора тем же правилом, каким сбор выбирает сессию, в backend/src/financial_ai/market_data/coverage.py per FR-054 (contradicts)
- [X] T129 Объявлять сессию собранной только при доработанном плане источников в backend/src/financial_ai/market_data/{ingest.py,runner.py} per FR-058 (contradicts)
- [X] T130 Покрыть испытаниями каждый из сквозных случаев в backend/tests/

## Phase 29: Догон многодневного окна и остановка в автосборе

- [X] T131 Датировать действие имени началом окна прогона в backend/src/financial_ai/market_data/ingest.py per FR-048 (contradicts)
- [X] T132 Датировать сверку связей днём обращения, а состав доски брать из свежих котировок, в backend/src/financial_ai/market_data/{ingest.py,links.py,repository.py} per FR-049 (contradicts)
- [X] T133 Различать прерванную сессию в автоматическом сборе в backend/src/financial_ai/market_data/{scheduler.py,advance.py,ingest.py} per FR-058 (contradicts)
- [X] T134 Записывать неспрошенный источник в журнал прогонов в backend/src/financial_ai/market_data/ingest.py per FR-050 (contradicts)
- [X] T135 Не называть дату следующего сбора, когда брать нечего, в backend/src/financial_ai/market_data/coverage.py per FR-054 (contradicts)
- [X] T136 Покрыть испытаниями каждый из пяти случаев в backend/tests/

## Phase 30: Найденное собственной проверкой

- [X] T137 Расширять действующий интервал имени назад вместо новой строки в backend/src/financial_ai/market_data/repository.py per FR-048 (contradicts)
- [X] T138 Не сверять связи на каждую сессию ежедневного цикла в backend/src/financial_ai/market_data/ingest.py per FR-049 (contradicts)
- [X] T139 Различить «соберём по расписанию» и «взять нечего» в артефакте Open Design и перенести в backend/src/financial_ai/market_data/coverage.py и frontend/src/widgets/catchup-section/CatchupSection.tsx per FR-054 (missing)
- [X] T140 Покрыть испытаниями рост строк имени, число обращений за связями и обе подписи в backend/tests/ и frontend/tests/

## Phase 31: Посессионный путь и два вида ожидания

- [X] T141 Датировать сверку связей днём обращения и в посессионном пути в backend/src/financial_ai/market_data/ingest.py per FR-049 (contradicts)
- [X] T142 Различить ожидание повтора и исчерпание попыток в backend/src/financial_ai/market_data/{advance.py,coverage.py} per FR-054 (contradicts)
- [X] T143 Возвращать прерванному прогону исход «прерван» после отметки перезапуска в backend/src/financial_ai/market_data/journal.py per FR-041 (contradicts)
- [X] T144 Покрыть испытаниями все три случая в backend/tests/

## Phase 32: Справочники и первичная загрузка против переименования

- [X] T145 Ключевать отраслевую принадлежность сущностью, а не именем, в backend/src/financial_ai/market_data/sources/reference.py per FR-048 (contradicts)
- [X] T146 Ключевать размеры лотов и якорь ISIN сущностью в backend/src/financial_ai/market_data/sources/securities.py per FR-048 (contradicts)
- [X] T147 Составлять имя ряда весов индекса из канонического имени бумаги в backend/src/financial_ai/market_data/sources/reference.py per FR-048 (contradicts)
- [X] T148 Держать один идентификатор на всю первичную загрузку в backend/src/financial_ai/market_data/backfill.py per FR-052 (contradicts)
- [X] T149 Ключевать наблюдения первичной загрузки сущностью в backend/src/financial_ai/market_data/backfill.py per FR-048 (contradicts)
- [X] T150 Покрыть испытаниями все пять случаев в backend/tests/

## Phase 33: Проверка фичи целиком

- [X] T151 Остановить расширение интервала имени у чужого интервала в backend/src/financial_ai/market_data/repository.py per FR-048 (contradicts)
- [X] T152 Починить проверку здоровья контейнера интерфейса в frontend/Dockerfile (contradicts)
- [X] T153 Покрыть испытаниями границу интервала имени в backend/tests/market_data/test_review_rules.py

## Phase 34: Остановка видна, а план показывает только своё

- [X] T154 Сохранять последнюю сессию прогона, чтобы лента источников не исчезала с экрана, в backend/src/financial_ai/market_data/{runner.py,scheduler.py} per FR-025 (contradicts)
- [X] T155 Убрать из плана источники, которых прогон не спрашивает, в артефакте Open Design и в backend/src/financial_ai/market_data/{ingest.py,advance.py,runner.py,scheduler.py} per FR-056a (contradicts)
- [X] T156 Покрыть испытаниями обе правки в backend/tests/

## Phase 35: Остановка как пауза

- [X] T157 Продолжать остановленный прогон в его же состоянии, не обнуляя счётчики, в backend/src/financial_ai/market_data/runner.py и backend/src/financial_ai/worker/routes/catchup.py per FR-058b (contradicts)
- [X] T158 Называть сессию с момента составления плана, чтобы лента не исчезала, в backend/src/financial_ai/market_data/{runner.py,scheduler.py} per FR-058c (contradicts)
- [X] T159 Не повторять задержанный источник после остановки в backend/src/financial_ai/market_data/ingest.py per FR-058d (contradicts)
- [X] T160 Назвать причину сетевого обрыва у источника позиций в backend/src/financial_ai/market_data/sources/positions_client.py per FR-002 (contradicts)
- [X] T161 Покрыть испытаниями все четыре правки в backend/tests/

## Phase 36: Не ходить за собранным и называть собранное числом

- [X] T162 Не запрашивать источник, уже собранный за эту сессию, в backend/src/financial_ai/market_data/{ingest.py,repository.py} per FR-058e (contradicts)
- [X] T163 Называть исход источника числом и при успехе в backend/src/financial_ai/market_data/{ingest.py,plan.py,runner.py,scheduler.py} per FR-058f (missing)
- [X] T164 Покрыть испытаниями обе правки в backend/tests/

## Phase 37: Продолжать с места и не молчать во время работы

- [X] T165 Продолжать прогон в порядке его сбора, а не в порядке показа, в backend/src/financial_ai/market_data/{runner.py,scheduler.py} per FR-058g (contradicts)
- [X] T166 Проверять остановку до цикла сессий в backend/src/financial_ai/market_data/ingest.py per FR-058h (missing)
- [X] T167 Показывать ход источника по инструментам в backend/src/financial_ai/market_data/{ingest.py,sources/positions.py} per FR-058i (missing)
- [X] T168 Покрыть испытаниями все три правки в backend/tests/

## Phase 38: Свежая сессия отдельно, история по порядку

- [X] T169 Собирать свежую сессию отдельным прогоном, а историю — по возрастанию дат, в backend/src/financial_ai/market_data/advance.py per FR-045 (contradicts)
- [X] T170 Покрыть испытаниями оба порядка в backend/tests/market_data/test_advance.py

## Phase 39: Остановка прерывает повторы, счётчик считает обращения

- [X] T171 Прерывать повторы обращения у источника позиций по команде остановки в backend/src/financial_ai/market_data/sources/positions_client.py per FR-058j (contradicts)
- [X] T172 Считать обращения, а не позиции в списке, в backend/src/financial_ai/market_data/sources/positions.py per FR-058i (contradicts)
- [X] T173 Покрыть испытаниями обе правки в backend/tests/

## Phase 40: Продолжение сохраняет режим прогона

- [X] T174 Продолжать прогон его же планом: ежедневный — ежедневным, ручной — ручным, в backend/src/financial_ai/market_data/runner.py per FR-058b (contradicts)
- [X] T175 Покрыть испытанием сохранение режима в backend/tests/

## Phase 41: Остановка доходит до всех клиентов, закрытое не спрашивается

- [X] T176 Прерывать повторы у клиента биржи и клиента ЦБ по команде остановки в backend/src/financial_ai/market_data/iss/client.py и backend/src/financial_ai/market_data/sources/cbr.py per FR-058j (contradicts)
- [X] T177 Не брать в работу сессии источник, уже закрытый за неё, в backend/src/financial_ai/market_data/ingest.py per FR-058k (contradicts)
- [X] T178 Покрыть испытаниями обе правки в backend/tests/

## Phase 42: Дата следующего сбора после разделения порядка

- [X] T179 Считать следующую сессию правилом разделённого порядка в backend/src/financial_ai/market_data/coverage.py per FR-054 (contradicts)
- [X] T180 Различить подпись у закрытой сессии в артефакте Open Design и в frontend/src/widgets/catchup-section/CatchupSection.tsx per FR-054 (missing)

## Phase 43: Известное объявляется в начале сессии

- [X] T181 Объявлять суточный пропуск и закрытые источники до первого обращения в backend/src/financial_ai/market_data/ingest.py per FR-058l (contradicts)
- [X] T182 Покрыть испытаниями оба объявления в backend/tests/market_data/test_review_rules.py

## Phase 44: Подпись под шкалой по артефакту

- [X] T183 Перенести подпись идущего прогона из артефакта: формы слова, без нулей, без текущей сессии в счёте, в frontend/src/widgets/catchup-section/CatchupSection.tsx и frontend/src/shared/lib/market-format.ts per FR-021 (partial)
- [X] T184 Покрыть испытаниями все три случая подписи в frontend/tests/collection-states.test.tsx

- [X] T185 Считать сессии источника тем же правилом, что и сессии группы, в backend/src/financial_ai/market_data/{completeness.py,coverage.py} per FR-032 (contradicts)
- [X] T186 Покрыть испытанием совпадение счёта группы и её источника в backend/tests/market_data/test_review_rules.py

- [X] T187 Различить три ответа о моменте ближайшего сбора в артефакте Open Design и в frontend/src/widgets/collection-calendar/CollectionCalendar.tsx per FR-054 (contradicts)
- [X] T188 Покрыть испытаниями отставание и неторговый день в frontend/tests/market-data-summary.test.tsx

- [X] T189 Объявлять известное только по источникам самой сессии, не затирая исход диапазонных, в backend/src/financial_ai/market_data/ingest.py per FR-058l (contradicts)
- [X] T190 Покрыть испытанием границу между сессионными и прогонными источниками в backend/tests/market_data/test_review_rules.py

- [X] T191 Считать закрытые сессии один раз на группу и отдавать обоим потребителям в backend/src/financial_ai/market_data/{completeness.py,coverage.py} per FR-032 (contradicts)
- [X] T192 Покрыть испытанием единственность расчёта в backend/tests/market_data/test_review_rules.py

## Phase 45: Сводка не обещает собрать собранное

- [X] T193 Не показывать неудачу за сессию, закрытую по этому источнику, в backend/src/financial_ai/market_data/coverage.py per FR-058m (contradicts)
- [X] T194 Не называть датой следующего сбора уже собранную сессию в артефакте Open Design, backend/src/financial_ai/market_data/coverage.py и обоих виджетах per FR-054 (contradicts)
- [X] T195 Убрать дублирующую пометку области у строки ленты в артефакте Open Design и frontend/src/widgets/catchup-section/SourceRail.tsx per FR-007 (contradicts)
- [X] T196 Покрыть испытаниями скрытие неудач и отсутствие даты в backend/tests/ и frontend/tests/

## Phase 46: Исправления повторного ревью

- [X] T197 Применять общее правило полноты в ежедневном и ручном сборе, включая позиции и диапазонные источники; сохранить покрытие при продолжении.
- [X] T198 Записывать остановку только выбранной незавершённой работы; отменять повторы ЦБ и дальнейшие запросы диапазона, сохраняя частичные строки незавершёнными.
- [X] T199 Вычислять ожидаемую дату на сборщике; показывать её в обоих виджетах, учитывать блокировку и фактическое закрытие сессии.
- [X] T200 Добавить регрессии остановки, продолжения, единого правила полноты и расписания; согласовать спецификацию и контракт.
- [X] T201 Выполнить полный `scripts/check.sh`, включая сборку образов: 809 backend, 61 emulator и 231 frontend тест; миграции, проверки типов, форматирования и сборка образов прошли.
- [X] T202 Показывать дату и местное время следующего сбора одной строкой; для сегодняшней даты писать «сегодня», в ожидаемый день добавлять короткое пояснение.

## Phase 47: Полнота данных и эффективность обращений — ревью 2026-09-20

Ревью `1f5664d` обнаружило скрытый недобор при полном покрытии в сводке и лишние обращения.
Зелёный гейт T201 не закрывает новые замечания. До их устранения модуль данных не считается
завершённым. Порядок, файлы и приёмка каждого шага — в [remediation-plan.md](./remediation-plan.md).

- [X] T203 Уточнить требования полноты и зафиксировать исходные данные и измерения: FR-031/032/032a-d/033/047/050/058e в `spec.md`, `coverage_version` и `market_coverage_boundary` в `data-model.md`, `requires_audit` в контракте сводки, исходный срез в `remediation-results.md`.
- [X] T204 Разбирать дробные числа ISS сразу в Decimal без промежуточного float: `parse_float=Decimal` в `iss/client.py:_loads`, четыре регрессии HTTP-пути в `test_precision.py`.
- [X] T205 Сохранять частичные глобальные ряды и ЦБ, передавая ошибки и незавершённость: `SourcePartialError`, `SeriesFetchError`, независимые ставка и кривая ЦБ, 8 регрессий в `test_partial_sources.py`.
- [X] T206 Различить сбой и отсутствие истории фьючерса; использовать полученные при поиске снимки: ошибка проб передаётся наружу, отрицательная сетка проб отсутствием не считается, кеш снимков прогона, остановка — `SourceStoppedError`.
- [X] T207 Сохранять позиции при ошибке/остановке и учитывать семейство в ключе повторного сбора.
- [X] T208 Ввести единое доказуемое правило полноты, включая диапазоны и старые исходы.
- [X] T209 Применить его к сводке, догону, готовности, объявлению полноты набора и устареванию.
- [X] T210 Ограничить план выбранными группами и собственными окнами источников.
- [X] T211 Уменьшить лишние HTTP/SQL-запросы и подтвердить результат сопоставимыми измерениями.
- [X] T212 Выполнить управляемый аудит и адресное восстановление накопленных пропусков.
- [X] T213 Подтвердить исправления регрессиями, полным scripts/check.sh и живыми сверками.
- [X] T214 Актуализировать документацию и записать доказательства итоговой готовности.

## Phase 48: Исправления ревью 2026-09-21 — последовательный план

Повторное [ревью](./review-2026-09-21.md) обнаружило R1–R7 и доказанные доступные пропуски.
Отметки Phase 47 сохраняют историю прежнего цикла и не подтверждают текущую готовность.
Порядок, файлы, регрессии, условия приёмки и запрос для исполнителя —
[remediation-plan-v2.md](./remediation-plan-v2.md). Доказательства записывать в
[remediation-results-v2.md](./remediation-results-v2.md). Выполнять по одному T сверху вниз.

- [X] T215 Зафиксировать целевые состояния, доказательства полноты, новую версию и безопасный переход в спецификации и контрактах. Доказательство: `remediation-results-v2.md`, раздел T215.
- [X] T216 Валидировать блоки, обязательные колонки, строки и даты ответов ISS; не принимать нарушение контракта за пустоту. Доказательство: `remediation-results-v2.md`, раздел T216.
- [X] T217 Исправить пагинацию с учётом фактического ограничения сервера и доказать равенство полного результата. Доказательство: `remediation-results-v2.md`, раздел T217.
- [X] T218 Сохранять основания по единицам работы и выполнить безопасную миграцию версии без автоматического ремонта истории. Доказательство: `remediation-results-v2.md`, раздел T218.
- [X] T219 Присваивать покрытие только проверенному результату источника и согласовать всех потребителей полноты. Доказательство: `remediation-results-v2.md`, раздел T219.
- [X] T220 Различить значение, подтверждённое отсутствие и неизвестный ответ в клиенте позиций. Доказательство: `remediation-results-v2.md`, раздел T220.
- [X] T221 Продолжать сбор оставшихся пар позиций; не блокировать другие даты пустым остатком и сохранять частичные результаты. Доказательство: `remediation-results-v2.md`, раздел T221.
- [X] T222 Использовать единый итог выбранной работы для состояния прогона и журнала, включая диапазоны и рестарт. Доказательство: `remediation-results-v2.md`, раздел T222.
- [X] T223 Показать честные итоги и непроверенные справочники в UI; обновлять сводку, журнал и календарь после прогона.
- [X] T224 Обеспечить единственного владельца всех путей сбора между процессами и проверить конфликт до HTTP. Доказательство: `remediation-results-v2.md`, раздел T224.
- [ ] T225 Обрабатывать точные оставшиеся пары ремонта; завершённый план повторять без запросов и изменения исхода.
- [ ] T226 Проверить сохранение бюджета, мягкую остановку и продолжение ремонта после холодного рестарта.
- [ ] T227 Ускорить сводку без изменения смысла; подтвердить SQL и время сопоставимыми измерениями.
- [ ] T228 Объединить обход доски для выбранных котировок и агрегатов с сохранением отдельных результатов.
- [ ] T229 Проверить ускорение Brent по историческому каталогу либо зафиксировать доказанный предел рабочего fallback.
- [ ] T230 Выполнить read-only аудит актуального окна и подготовить конкретный объём, оценку и бюджет ремонта.
- [ ] T231 Выполнить только разрешённый ремонт и сопоставить БД, сводку, журнал, готовность ML и набор.
- [ ] T232 Выполнить полный scripts/check.sh, живые сверки и визуальную приёмку, сохранив ограничения отдельно.
- [ ] T233 Актуализировать документацию и подтвердить итог каждого замечания доказательствами; не скрывать незавершённую приёмку.
