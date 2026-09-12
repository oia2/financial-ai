---

description: "Задачи фичи: интерфейс рыночных данных и общая навигация"
---

# Tasks: Интерфейс рыночных данных и общая навигация

**Input**: Design documents from `/specs/006-market-data-ui/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: включены. Принцип IV конституции требует сопровождать новую существенную логику
тестами, и в проекте это уже сложившаяся практика: контрактные тесты в
`backend/tests/contract/`, тесты состояний экрана в `frontend/tests/`.

**Organization**: задачи сгруппированы по пользовательским историям, чтобы каждую можно
было довести и проверить отдельно.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно выполнять параллельно (разные файлы, нет незакрытых зависимостей)
- **[Story]**: к какой истории относится задача (US1–US4)
- В описании — точный путь к файлу

## Path Conventions

Веб-приложение: `backend/src/financial_ai/`, `backend/tests/`, `frontend/src/`,
`frontend/tests/`. Раскладка существующая, новых каталогов верхнего уровня нет.

Артефакт Open Design (источник истины по интерфейсу) лежит в проекте
«Портфель FINANCIAL AI»: `market-data.html`, `financial-ai-portfolio.html`,
`market-data-design.md`, `design-assets/`.

---

## Phase 1: Setup — артефакты правятся раньше кода

**Purpose**: привести спецификации фичи 005 в соответствие тому, что фича 006 от них
потребует, и перенести стили из утверждённого дизайна. Принцип III: исходный артефакт
правится до кода, а не после.

- [X] T001 [P] Добавить в `specs/005-market-data-control/contracts/coverage-report.md` поле `looks_collected_but_empty` у группы и блок `catchup_window` (`date_from`, `date_till`, `sessions`) на верхнем уровне ответа, с обоснованием из research.md R2.1 и R2.2
- [X] T002 [P] Уточнить в `specs/005-market-data-control/contracts/catchup-control.md`, что `reason` — короткая человекочитаемая причина, а технические подробности остаются в журнале (research.md R2.3)
- [X] T003 [P] Уточнить FR-005 и FR-012 в `specs/005-market-data-control/spec.md`: ход и признак «покрыто, но пусто» предназначены для показа человеку в интерфейсе, а не только для чтения в терминале
- [X] T004 Перенести стили оболочки и раздела из артефакта в `frontend/src/app/styles/design.css`: правила `design-assets/financial-ai-shell.css` и `design-assets/market-data.css` целиком, с сохранением имён классов и порядка (Принцип VIII; переносится, а не пересказывается)

**Checkpoint**: спецификации 005 описывают то, что будет реализовано; стили артефакта в проекте.

---

## Phase 2: Foundational — без этого ни одна история не работает

**Purpose**: поля контракта, публичные маршруты и каркас фронтенда.

**⚠️ CRITICAL**: до завершения фазы работа по историям не начинается.

### Backend: недостающие поля контракта 005

- [X] T005 [P] Добавить `looks_collected_but_empty` в `GroupCoverage.to_dict()` в `backend/src/financial_ai/market_data/coverage.py`, используя существующее свойство с порогом 1%
- [X] T006 [P] Добавить блок `catchup_window` (границы и число сессий окна догона) в ответ `/internal/coverage` — в `backend/src/financial_ai/market_data/coverage.py`, где `build_report` уже держит календарь и настройки; окно берётся из `Settings.catchup_window_sessions`, тем же источником, каким пользуется планирование прогона (маршрут worker правки не потребовал)
- [X] T007 [P] Заменить `repr(error)` в `CatchupState.reason` на короткую человекочитаемую причину в `backend/src/financial_ai/market_data/runner.py`; полный трейсбек остаётся в `logger.exception`
- [X] T008 Дополнить контрактные тесты worker в `backend/tests/contract/test_worker_coverage.py` (новые поля, отсутствие полей истории у справочника) и `backend/tests/contract/test_worker_catchup.py` (причина без адресов и текста исключения)

### Backend: публичные маршруты

- [X] T009 Не описывать DTO ответов повторно: `backend/src/financial_ai/api/routes/market_data.py` передаёт ответ worker как есть. Форма задана контрактом 005, а модель, собранная по умолчаниям, стёрла бы значимую разницу между «поля нет» и «поле равно null» (FR-014, FR-015). В `schemas.py` описан только разбор тела запуска
- [X] T010 Реализовать `backend/src/financial_ai/api/routes/market_data.py`: `GET /market-data/coverage`, `GET /market-data/catchup`, `POST /market-data/catchup`, `DELETE /market-data/catchup` — передачей во внутренний канал worker по образцу `portfolio.py:150-158`, с `Cache-Control: no-store` и переводом недоступности worker в `503 worker_unavailable`
- [X] T011 Зарегистрировать роутер с префиксом `/api` в `backend/src/financial_ai/api/app.py` рядом с `health`, `portfolio`, `settings`
- [X] T012 Написать контрактные тесты `backend/tests/contract/test_market_data_api.py`: четыре маршрута, перевод кодов `catchup_already_running` / `backfill_required` / `unknown_group` / `invalid_range`, `calendar_empty`, и `worker_unavailable` при `httpx.ConnectError` (respx, как в `test_portfolio_refresh.py`)

### Frontend: каркас

- [X] T013 [P] Добавить `apiDelete` в `frontend/src/shared/api/client.ts` рядом с `apiGet`/`apiPost`/`apiPut`, с тем же различением `ServerUnreachableError` и `ApiError`
- [X] T014 [P] Реализовать маршрутизатор в `frontend/src/app/router/` на `history.pushState` + `popstate` + `useSyncExternalStore`: два маршрута `/` и `/market-data`, перехват кликов по внутренним ссылкам (research.md R3)
- [X] T015 [P] Создать сущность `frontend/src/entities/market-data/` (`types.ts`, `api.ts`, `index.ts`): типы сводки и состояния прогона по `data-model.md`, запросы через `apiGet`/`apiPost`/`apiDelete`, ключи запросов
- [X] T016 Перестроить `frontend/src/app/App.tsx` в оболочку: раздел выбирается маршрутом, добавлена заготовка `frontend/src/pages/market-data/MarketDataPage.tsx`

**Checkpoint**: публичные маршруты отвечают, раздел открывается по адресу, истории можно вести параллельно.

---

## Phase 3: User Story 1 — человек видит полноту рыночных данных (Priority: P1) 🎯 MVP

**Goal**: раздел показывает сводку по пяти группам так, что расхождение «покрыто, но пусто»
видно без обращения к хранилищу.

**Independent Test**: открыть раздел на стенде с частично собранными данными и сверить
каждую группу с выводом `cli coverage`, не заглядывая в таблицы (quickstart С1–С3).

### Тесты

- [X] T017 [P] [US1] Тесты сводки в `frontend/tests/market-data-summary.test.tsx`: пять групп, две колонки, отсутствие полей истории у справочника, прочерк вместо `null`, отсутствие значений наблюдений на экране

### Реализация

- [X] T018 [US1] Реализовать `frontend/src/widgets/completeness-table/CompletenessTable.tsx` по разметке артефакта: колонки «Группа», «Покрытие», «Со значениями», «Период данных», кнопка сведений; имена классов и `data-od-id` из `market-data.html`
- [X] T019 [US1] Показать группу без истории в том же виджете: вместо числовых полей — объяснение неприменимости, без нулей; `rows_total`/`rows_with_values` = `null` → прочерк с пояснением (FR-014, FR-015)
- [X] T020 [P] [US1] Реализовать уведомление о расхождении `frontend/src/widgets/completeness-table/EmptyValuesAlert.tsx` по `data-od-id="empty-values-alert"`, включаемое полем `looks_collected_but_empty`, и выделение строки группы (FR-016)
- [X] T021 [P] [US1] Реализовать панель сведений о группе `frontend/src/widgets/completeness-table/GroupDetailsDrawer.tsx` на семантическом `dialog`: те же поля в развёрнутом виде, закрытие по Escape, возврат фокуса вызвавшей кнопке (FR-017, FR-053)
- [X] T022 [US1] Собрать раздел в `frontend/src/pages/market-data/MarketDataPage.tsx`: заголовок, дата снимка, сводка; действие обновления в шапке перечитывает сводку (FR-018)

**Checkpoint**: полнота данных читается с экрана; догон ещё не управляется.

---

## Phase 4: User Story 2 — человек ведёт догон из интерфейса (Priority: P1)

**Goal**: полный цикл «запустить — увидеть ход — остановить — продолжить» без терминала.

**Independent Test**: пройти цикл на стенде и убедиться, что закрытые сессии не собираются
заново, а состояние «остановлен» появляется только после ответа сервера (quickstart С4–С6).

### Тесты

- [X] T023 [P] [US2] Тесты шести состояний прогона в `frontend/tests/catchup-states.test.tsx`: `idle`, `running`, `stopping`, `stopped`, `finished`, `failed` — состав чисел, доступные действия, отсутствие действий у `stopping`
- [X] T024 [P] [US2] Тесты формы запуска в `frontend/tests/catchup-launch.test.tsx`: умолчания (все группы, всё окно), предзаполнение дат из `catchup_window`, невозможность отправить пустой выбор групп

### Реализация

- [X] T025 [US2] Реализовать чтение состояния прогона в `frontend/src/entities/market-data/api.ts`: `useQuery` с `refetchInterval` 3 с при `running`/`stopping` и `false` в остальных состояниях (research.md R5)
- [X] T026 [US2] Реализовать `frontend/src/widgets/catchup-section/CatchupSection.tsx`: шесть состояний по `contracts/ui-states.md`, счётчики «Закрыто из», «Осталось обработать», «Не закрыто», текущая сессия с ориентиром длительности
- [X] T027 [US2] Реализовать индикатор хода в `frontend/src/widgets/catchup-section/ProgressTrack.tsx`: два сегмента (закрытые и незакрывшиеся), `role="progressbar"` с `aria-valuemax=requested`, `aria-valuenow=closed` и текстовым описанием; длина меняется только при новом `closed` от сервера (FR-030, FR-031)
- [X] T028 [US2] Реализовать форму запуска `frontend/src/features/catchup-launch/LaunchDrawer.tsx` на `dialog` по разметке артефакта: выбор групп, флажок «Всё доступное окно», поля диапазона, предупреждение о длительности работы (FR-019–FR-022)
- [X] T029 [US2] Реализовать остановку и продолжение в `frontend/src/features/catchup-stop/`: остановка одним действием без подтверждения, переход в `stopped` только по ответу сервера, продолжение новым запуском (FR-023–FR-025, FR-032)
- [X] T030 [US2] Обеспечить чтение состояния при входе в раздел и возврате к нему в `frontend/src/pages/market-data/MarketDataPage.tsx` (FR-035)

**Checkpoint**: догон полностью управляется из раздела рыночных данных.

---

## Phase 5: User Story 3 — разделы живут в общей оболочке (Priority: P2)

**Goal**: два раздела в общей шапке, идущий сбор виден из любого из них.

**Independent Test**: перейти между разделами во время прогона и убедиться, что баннер виден
в обоих и переживает переход и перезагрузку (quickstart С6).

### Тесты

- [X] T031 [P] [US3] Тесты навигации в `frontend/tests/navigation.test.tsx`: два пункта, активный отмечен `aria-current="page"`, прямой адрес и переход «назад» открывают тот же раздел, баннер процесса виден в обоих разделах

### Реализация

- [X] T032 [US3] Добавить навигацию в `frontend/src/widgets/app-header/AppHeader.tsx` по разметке артефакта (`app-navigation`, `nav-portfolio`, `nav-market-data`), сохранив состав и порядок действий справа (FR-001, FR-003)
- [X] T033 [US3] Реализовать общий баннер процесса `frontend/src/widgets/process-rail/ProcessRail.tsx` по `data-od-id="global-process-banner"`: состояние, `closed / requested`, переход к процессу; скрыт, когда прогон неактивен (FR-006, FR-007)
- [X] T034 [US3] Поднять владельца опроса состояния прогона выше маршрутов в `frontend/src/app/App.tsx`, чтобы баннер не гас при переходе в портфель (research.md R5)
- [X] T035 [P] [US3] Добавить переход между разделами через `document.startViewTransition()` в `frontend/src/app/router/`, сохранив имена `view-transition-name` и длительности 140/240 мс; без поддержки API переход мгновенный (research.md R4)
- [X] T036 [P] [US3] Подписать настройку интервала как относящуюся к брокерскому счёту в `frontend/src/features/refresh-interval-setting/RefreshIntervalForm.tsx` и убедиться, что в разделе рыночных данных она ни на что не влияет (FR-004, FR-005)

**Checkpoint**: оболочка общая, процесс виден отовсюду.

---

## Phase 6: User Story 4 — отказы и пограничные состояния объясняются честно (Priority: P2)

**Goal**: каждый из десяти случаев артефакта получает своё объяснение, и ни одна причина не
выдаётся за другую.

**Independent Test**: воспроизвести каждый ответ и сверить показанное объяснение с
`contracts/ui-states.md` (quickstart С8–С13).

### Тесты

- [X] T037 [P] [US4] Тесты пограничных случаев в `frontend/tests/catchup-edge-cases.test.tsx`: десять случаев из `contracts/ui-states.md`, каждый со своим сообщением

### Реализация

- [X] T038 [US4] Реализовать состояния пустого хранилища и «догонять нечего» в `frontend/src/pages/market-data/MarketDataPage.tsx`: объяснение про первичную загрузку с недоступным запуском (`calendar_empty`, `backfill_required`) и спокойное подтверждение полноты (FR-026, FR-038)
- [X] T039 [US4] Реализовать показ отказов запуска в `frontend/src/features/catchup-launch/LaunchDrawer.tsx`: `catchup_already_running` не затирает счётчики идущего прогона, `unknown_group` объясняется как устаревший запрос (FR-038, FR-039)
- [X] T040 [US4] Реализовать уведомление о сужении диапазона `frontend/src/features/catchup-launch/ClampNotice.tsx`: введённый и принятый диапазоны рядом, сообщение не исчезает по таймеру (FR-040)
- [X] T041 [US4] Реализовать валидацию дат в форме запуска: неразбираемая дата и «начало позже конца» — ошибка у поля, значение сохранено, фокус к началу диапазона (FR-041)
- [X] T042 [US4] Реализовать замену показанного `running` ответом `idle` в `frontend/src/widgets/catchup-section/CatchupSection.tsx`: прежний статус отбрасывается, причина названа возможной, а не установленной (FR-035)
- [X] T043 [US4] Реализовать различение недоступности сборщика и обрыва связи с сервером: `worker_unavailable` и `ServerUnreachableError` дают разные сообщения, показанные ранее значения помечаются как последние известные (FR-036, FR-042, FR-047b)
- [X] T044 [US4] Проверить формулировки сообщений на отсутствие технических подробностей, внутренних адресов и конфигурации во всех состояниях отказа (FR-043)

**Checkpoint**: все истории закрыты; экран честен во всех известных состояниях.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T045 [P] Актуализировать `README.md`: раздел рыночных данных, новые публичные маршруты (Принцип IX)
- [X] T046 [P] Актуализировать `AGENTS.md`: границы компонентов — интерфейс ходит только в `backend-api`, worker остаётся внутренним (Принцип IX)
- [X] T047 Сверить реализацию с артефактом Open Design по `specs/006-market-data-ui/contracts/ui-states.md`: состав и имена элементов, а не общее впечатление; зафиксировать единственное отступление по способу запуска перехода (Принцип VIII)
- [X] T048 Проверить доступность и адаптивность: высота элементов от 44 px, Escape и возврат фокуса у обеих панелей, `prefers-reduced-motion`, ширина от 360 px без горизонтальной прокрутки, состояния переданы текстом, а не только цветом (FR-051–FR-057)
- [X] T049 Пройти сценарии `specs/006-market-data-ui/quickstart.md` на поднятом стенде, включая С0 (внутренний канал недоступен снаружи), С7 (перезапуск сборщика) и С13 (сборщик остановлен)
- [X] T050 Прогнать `scripts/check.sh` целиком, включая сборку образов; частичный прогон гейтом не считается

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: без зависимостей, начинается сразу. T004 не блокирует backend-задачи
- **Foundational (Phase 2)**: зависит от Phase 1 (T001–T003 описывают поля, которые здесь реализуются) — **блокирует все истории**
- **US1 (Phase 3)** и **US2 (Phase 4)**: обе P1, обе зависят только от Phase 2, друг от друга не зависят
- **US3 (Phase 5)**: зависит от Phase 2; баннер процесса (T033, T034) использует чтение состояния из T025, поэтому идёт после US2 либо реализуется с временной заглушкой состояния
- **US4 (Phase 6)**: зависит от Phase 2; T039–T042 правят компоненты US2, поэтому выполняются после Phase 4
- **Polish (Phase 7)**: после всех историй

### Внутри истории

- Тесты пишутся до реализации и сначала падают
- Сущность и запросы — до виджетов
- Виджеты — до сборки страницы
- Реализация — до сверки с артефактом

### Parallel Opportunities

- T001, T002, T003 — три разных файла спецификаций, параллельны
- T005, T006, T007 — три разных модуля backend, параллельны; T008 — после них
- T013, T014, T015 — три независимых модуля фронтенда, параллельны
- T020 и T021 — разные файлы виджета сводки, параллельны после T018
- T023 и T024 — разные файлы тестов, параллельны
- T035 и T036 — разные файлы, параллельны
- T045 и T046 — разные документы, параллельны
- US1 и US2 могут вестись разными людьми сразу после Phase 2

---

## Parallel Example: Phase 2

```bash
# Три поля контракта — три разных модуля:
Task: "looks_collected_but_empty в backend/src/financial_ai/market_data/coverage.py"
Task: "catchup_window в backend/src/financial_ai/worker/routes/coverage.py"
Task: "человекочитаемая причина в backend/src/financial_ai/market_data/runner.py"

# Каркас фронтенда — три независимых модуля:
Task: "apiDelete в frontend/src/shared/api/client.ts"
Task: "маршрутизатор в frontend/src/app/router/"
Task: "сущность в frontend/src/entities/market-data/"
```

---

## Implementation Strategy

### MVP (US1)

1. Phase 1 — артефакты и стили
2. Phase 2 — поля контракта, публичные маршруты, каркас фронтенда
3. Phase 3 — сводка полноты
4. **Остановиться и проверить**: сценарии С1–С3 quickstart; слепота к состоянию данных снята
5. Этого уже достаточно, чтобы фича приносила пользу: догон остаётся управляемым из терминала

### Инкрементальная поставка

1. Phase 1 + Phase 2 → основание готово
2. + US1 → полнота видна (MVP)
3. + US2 → догон управляется из интерфейса
4. + US3 → процесс виден из любого раздела
5. + US4 → все известные отказы объяснены
6. Phase 7 → документация, сверка с дизайном, гейт

### Замечание о порядке P1-историй

US1 и US2 обе P1 и независимы, но US1 дешевле и снимает главную боль — слепоту к
состоянию данных, породившую дефекты фичи 005. Если делать по одной, начинать стоит с неё.

---

## Notes

- `[P]` — разные файлы, нет незакрытых зависимостей
- Разметка и стили **переносятся** из артефакта Open Design, а не воспроизводятся по
  памяти; при отсутствии нужного состояния артефакт сначала дополняется в инструменте
- Демонстрационные данные прототипа (`market-data-runtime.js`, `localStorage`) не
  переносятся ни в каком виде
- Коммит после каждой задачи или логической группы
- На любом чекпойнте можно остановиться и проверить историю отдельно

---

## Phase 8: Convergence

**Purpose**: закрыть расхождения между утверждёнными артефактами и реализацией, найденные
сверкой 2026-09-10. Три из шести — отступления от артефакта Open Design, не
зафиксированные ни в коде, ни в `contracts/ui-states.md`.

- [X] T051 CRITICAL Перенести кнопку запуска в заголовок раздела `frontend/src/pages/market-data/MarketDataPage.tsx` (`configure-catchup` артефакта): «Запустить догон» в покое, «Параметры догона» вторичным стилем во время работы, «Нужна первичная загрузка» и `disabled` при пустом хранилище — per Constitution VIII, FR-019, contracts/ui-states.md §2 (missing)
- [X] T052 CRITICAL Сделать «Продолжить догон» немедленным запуском с группами прошлого прогона, без открытия формы, с сообщением «Продолжение запущено: только незакрытые сессии» — как в артефакте `design-assets/market-data.js`; правится в `frontend/src/pages/market-data/MarketDataPage.tsx` и `frontend/src/features/catchup-launch/useCatchupControl.ts` — per Constitution VIII, FR-025 (contradicts)
- [X] T053 Убрать предложение нового прогона в состоянии «догонять нечего» в `frontend/src/widgets/catchup-section/CatchupSection.tsx`: описание «Догонять нечего», уведомление «Все сессии окна покрыты», кнопка запуска не показывается — per FR-038, US4/AC2 (partial)
- [X] T054 Перечитывать состояние прогона при входе в раздел «Рыночные данные» и возврате к нему — сейчас запрос живёт в оболочке и при неактивном прогоне не обновляется; правится в `frontend/src/entities/market-data/api.ts` или `frontend/src/app/App.tsx` — per FR-035, US2/AC7 (partial)
- [X] T055 Убрать неиспользуемый `dismissPageNotice` из `frontend/src/features/catchup-launch/useCatchupControl.ts` либо задействовать его в разметке — per Constitution II (unrequested)
- [X] T056 Записать в `specs/006-market-data-ui/contracts/ui-states.md` §6 отступление по подписи даты снимка: артефакт подписывает её «Снимок стенда», в продукте — «Состояние на», потому что сводка не снимок стенда — per Constitution VIII (partial)

Найдено владельцем при просмотре реализации 2026-09-10, добавлено в эту же фазу:

- [X] T057 Перечитывать сводку полноты, когда прогон перешёл из активного состояния в завершённое, в `frontend/src/pages/market-data/MarketDataPage.tsx`. FR-037 запрещает **выводить** сводку из статуса прогона, но не требует, чтобы человек жал «Обновить» ради результата только что завершённого сбора: числа берутся из настоящего чтения с сервера — per FR-037 (partial)
- [X] T058 Подписать состояние `finished` как «Догон завершён» вместо «Диапазон завершён» в `frontend/src/widgets/catchup-section/CatchupSection.tsx`; отступление от артефакта записать в `contracts/ui-states.md` §6 — решение владельца проекта 2026-09-10 — per Constitution VIII (contradicts)

**Checkpoint**: реализация соответствует артефакту либо каждое отступление зафиксировано с причиной.
