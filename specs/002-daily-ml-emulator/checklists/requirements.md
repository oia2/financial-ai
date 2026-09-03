# Specification Quality Checklist: Эмулятор Daily ML

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-29
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded (см. раздел «Вне объёма»)
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

Итерация 1 (2026-08-29).

Разрешено на этапе спецификации по согласованию с владельцем проекта:

- граница данных на входе — только `decision_date`, вселенная у эмулятора;
- способ обмена — HTTP-эндпоинт в контейнере;
- идентификатор актива — `asset_id` + `price_series_id`;
- объём правки `README.md` / `AGENTS.md` — фиксируется возможность поднимать систему с
  эмулятором, без объявления контейнера `Daily ML` реализованным.

Итерация 5 (2026-08-29): закрыта фаза Convergence (T041–T044) после `/speckit-converge`.
Тестов стало 69. Внесено:

- **SC-007 переформулирован** (Принцип III — исправляется артефакт, а не подгоняется
  результат). Прежняя формулировка «гейт проходит целиком» была некорректной: шаг
  `frontend: prettier` падает по причине вне фичи. Диагноз — отсутствие `.gitattributes`
  при `core.autocrlf = true`; `prettier --write` починкой не является, git вернёт CRLF
  при следующем checkout. Настоящее лечение затрагивает весь репозиторий и по Принципу VI
  требует отдельной ветки `fix/`. Отступление описано в `spec.md` отдельным разделом;
- в `plan.md` зафиксировано появление каталога `docs/` и удаление `source/`;
- в контракт добавлены коды `404` и `405` с общей формой ошибки; полный перечень кодов
  теперь `200, 404, 405, 422`, недокументированных нет (проверено программно);
- добавлены тесты: переопределение `DAILY_ML_EMULATOR_MODEL_ID`, ответы `404` и `405`.

Итерация 4 (2026-08-29): фича реализована, статус спецификации — `Delivered`.
Все 40 задач `tasks.md` закрыты. Проверено на живом контейнере: SC-002 — 0,008 с при
вселенной в 200 активов (порог 1 с), SC-003 — детерминизм сохраняется после
перезапуска, SC-005 — `emulated: true` в каждом успешном ответе, SC-006 — в окружении
контейнера нет ни токенов, ни `DATABASE_URL`, SC-009 — недокументированных полей 0 и
описанных, но отсутствующих 0 (сверка выполнена программно, не на глаз).

Отступление от Принципа IV, зафиксировано явно: шаг `frontend: prettier` в
`scripts/check.sh` не проходит. Расхождение предсуществующее и к фиче отношения не
имеет — `git diff main -- frontend/` пуст, файлы побайтово совпадают с `main`, а
prettier расходится с ними в переводах строк (CRLF/LF) на 40 файлах. Правка не внесена:
она вне объёма фичи и затронула бы весь frontend. Остальные шаги гейта проходят.
Шаг `docker compose build` в этом окружении обрывается на `pnpm install` образа
frontend из-за отсутствия доступа к реестру npm; образы `daily-ml-emulator` и
`backend-api` собираются успешно.

По ходу реализации исправлены артефакты (Принцип III — сначала артефакт, потом код):

- `data-model.md` §5: путь вселенной по умолчанию изменён с `/app/universe/default.json`
  на относительный `universe/default.json`, иначе локальный запуск из quickstart падал;
- `quickstart.md` С6/С7: заменены плейсхолдеры имени образа, добавлена оговорка про
  подмену пути в Git Bash на Windows, уточнены ожидаемые код выхода и сообщение;
- `contracts/daily-ml-emulator-api.md`: в таблицу полей добавлена строка `items`.

Итерация 3 (2026-08-29): добавлены требования к описанию эмулятора, инструкции по
запуску и документации эндпоинтов (FR-019…FR-023, User Story 3, SC-008…SC-010).
Уточнения не потребовались: размещение документации задано уже сложившейся в проекте
конвенцией — `contracts/*.md` и `quickstart.md` рядом со спецификацией фичи, схема
эндпоинтов отдаётся самим сервисом, как у `backend-api` и `backend-worker`. Требования
к `README.md` / `AGENTS.md` перенумерованы в FR-024/FR-025 без изменения смысла.

Итерация 2 (2026-08-29): **Q1 / FR-010** закрыт. Ранжирование зависит от даты решения:
разные даты дают разный порядок, одна и та же дата — всегда один и тот же ответ.
Зависимость реализуется тривиальным детерминированным правилом от даты, без рыночных
данных. Открытых [NEEDS CLARIFICATION] не осталось.

Формулировки «HTTP-эндпоинт» и «контейнер» оставлены в спецификации сознательно: это
согласованные с владельцем проекта границы фичи и явное требование пользователя, а не
выбор реализации.
