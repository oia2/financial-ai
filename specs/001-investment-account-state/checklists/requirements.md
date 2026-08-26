# Specification Quality Checklist: Investment Account State

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-26
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
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`

### Открытые вопросы — закрыты (сессия уточнений 2026-08-26)

Все три маркера [NEEDS CLARIFICATION] сняты; принятые решения зафиксированы в
разделе `## Clarifications` спецификации и в соответствующих требованиях.

| # | Требование | Вопрос | Решение |
|---|------------|--------|---------|
| Q1 | FR-021 | Способ авторизации доступа к T-Bank API | Персональный read-only токен Т-Банк Invest из переменной окружения `TBANK_INVEST_READ_TOKEN` на стороне сервера; в UI не вводится, в хранилище не попадает |
| Q2 | FR-025 | Один брокерский счёт или несколько | Ровно один счёт; выбор и агрегация по нескольким счетам вне объёма |
| Q3 | FR-006 | Политика обновления состояния | Оба режима: фоновое автообновление по настраиваемому интервалу + ручное обновление |
| Q4 | FR-020, FR-024 | Судьба сценария подключения брокера в UI (следствие Q1) | User Story 2 «Подключение брокерского счёта» исключена из объёма; заменена историей автообновления. Подключение определяется конфигурацией сервера |
| Q5 | FR-031, FR-035 | Как задаётся интервал автообновления (следствие Q3) | Настройка в веб-интерфейсе: свободный ввод целого числа секунд, диапазон 15–3600, значение по умолчанию 60 с |
| Q6 | FR-037, FR-038 | Как сигнализировать обрыв связи (следствие Q3) | Два различимых состояния: «нет связи с сервером Financial AI» и «не удалось обновить портфель» (сбой T-Bank API) |

### Итерации валидации

**Итерация 1 (2026-08-26)** — проверены все пункты.

- Content Quality: пройдено. `PostgreSQL` был убран из раздела Dependencies как деталь
  реализации; хранилище описано нейтрально («внутреннее хранилище системы»).
  Упоминание T-Bank API сохранено намеренно — это внешняя система из постановки
  задачи, а не выбор технологии.
- Requirement Completeness: пройдено, кроме пункта о маркерах [NEEDS CLARIFICATION] —
  см. таблицу открытых вопросов выше.
- Feature Readiness: пройдено. Каждая user story имеет Independent Test и acceptance
  scenarios; SC-001…SC-009 измеримы и не привязаны к технологиям.

**Итерация 2 (2026-08-26, после сессии уточнений)** — перепроверены все пункты.

- Content Quality: пройдено. Имя переменной окружения `TBANK_INVEST_READ_TOKEN`
  оставлено в спецификации намеренно: это согласованный контракт конфигурации
  с владельцем проекта, а не выбор технологии реализации.
- Requirement Completeness: пройдено полностью — маркеров [NEEDS CLARIFICATION]
  не осталось. Добавлены FR-031…FR-040, SC-010…SC-012 и новые edge cases,
  покрывающие автообновление, настройку интервала и различение двух классов сбоев.
- Feature Readiness: пройдено. User Story 2 заменена на «Автоматическое поддержание
  актуальности состояния счёта» с собственным Independent Test и acceptance
  scenarios; User Story 4 расширена сценариями обрыва связи с сервером.

### Границы, принятые как решения (не как предположения)

- Допустимый диапазон интервала автообновления 15–3600 с (FR-031) и порог устаревания
  «max(3 × интервал, 180 с)» (FR-040) выбраны исходя из лимитов запросов T-Bank Invest API
  и подлежат подтверждению на этапе планирования по фактическим лимитам. Оба зафиксированы
  в спецификации и в разделе Assumptions, а не оставлены неявными.
- Автообновление считается всегда включённым: отдельная возможность его отключить
  в объём фичи не входит.

### Замечание по Принципу VI Constitution

Реализация ведётся в ветке `feature/investment-account-state` (создана 2026-08-26).
Спецификация и связанные артефакты перенесены в эту ветку; `main` остаётся
интеграционной веткой.
