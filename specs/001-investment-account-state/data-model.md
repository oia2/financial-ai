# Data Model: Investment Account State

**Дата**: 2026-08-26 | **Спека**: [spec.md](./spec.md) | **План**: [plan.md](./plan.md)

СУБД — PostgreSQL 17, миграции — Alembic. Все временные метки `TIMESTAMPTZ` в UTC.
Все денежные, ценовые и количественные величины — `NUMERIC(28, 9)`; `float`/`double`
не используются (обоснование — [research.md §5](./research.md)).

Сущности спецификации отображаются на таблицы так:

| Сущность спеки | Таблица |
|---|---|
| Инвестиционный счёт | `investment_account` |
| Брокерское подключение | `broker_sync_state` (статус доступа) + `investment_account` (маскированная идентификация) |
| Состояние счёта (снимок) | `account_state` (единственная актуальная строка) |
| Позиция | `portfolio_position` |
| Инструмент | денормализован в `portfolio_position` (см. §6) |
| Настройка обновления | `account_refresh_settings` |

Отдельной таблицы пользователей нет: на этом этапе пользователя как сущности не
существует — есть только токен доступа в конфигурации сервера и ровно один счёт (FR-025).
Поле `user_id` намеренно не вводится до появления аутентификации (решение владельца
проекта от 2026-08-26, см. plan.md §9).

---

## 1. `investment_account`

Единственный брокерский счёт (FR-025).

| Поле | Тип | Ограничения | Требование |
|---|---|---|---|
| `id` | `smallint` | PK, `CHECK (id = 1)` | singleton |
| `broker_account_id` | `text` | `NOT NULL` | идентификатор счёта в T-Invest |
| `masked_id` | `text` | `NOT NULL` | «•• 4821» — только маскированный вид (FR-022) |
| `display_name` | `text` | `NOT NULL` | «Основной брокерский счёт» |
| `currency` | `char(3)` | `NOT NULL DEFAULT 'RUB'` | валюта счёта |
| `created_at` | `timestamptz` | `NOT NULL DEFAULT now()` | |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | |

**Правила**

- Полный номер договора не хранится и не передаётся наружу (FR-022, SC-009). Из ответа
  брокера сохраняется только маскированное представление.
- Токен доступа здесь **не хранится** — он приходит из окружения (FR-021, FR-023).

---

## 2. `account_state`

Актуальное состояние счёта — одна строка, заменяемая целиком (FR-007, FR-008).

| Поле | Тип | Ограничения | Требование |
|---|---|---|---|
| `id` | `smallint` | PK, `CHECK (id = 1)` | singleton |
| `account_id` | `smallint` | FK → `investment_account.id`, `NOT NULL` | FR-010 |
| `captured_at` | `timestamptz` | `NOT NULL` | момент получения от брокера (FR-003) |
| `total_value` | `numeric(28,9)` | `NOT NULL` | общая стоимость (FR-011) |
| `cash` | `numeric(28,9)` | `NOT NULL` | остаток денежных средств |
| `positions_cost_basis` | `numeric(28,9)` | `NOT NULL` | суммарная средняя стоимость позиций — база для % P&L |
| `unrealized_pnl` | `numeric(28,9)` | `NOT NULL` | нереализованный P&L, абсолютный |
| `positions_count` | `integer` | `NOT NULL`, `CHECK (>= 0)` | |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | |

**Правила**

- `unrealized_pnl_percent` **не хранится**: при `positions_cost_basis = 0` процент не
  определён и не должен превращаться в ложный ноль (Edge Case спеки). Вычисляется в домене
  и отдаётся как `null`, когда база нулевая.
- Доля денежных средств (`cash / total_value`) не хранится: при `total_value = 0` деления
  не происходит, доля отдаётся как `0`.
- Запись выполняется только после успешной валидации ответа брокера (FR-004).

---

## 3. `portfolio_position`

Позиции текущего состояния. Полностью замещаются в той же транзакции, что и
`account_state` (FR-008).

| Поле | Тип | Ограничения | Требование |
|---|---|---|---|
| `id` | `bigserial` | PK | |
| `state_id` | `smallint` | FK → `account_state.id` `ON DELETE CASCADE`, `NOT NULL` | |
| `instrument_uid` | `text` | `NOT NULL` | идентификатор инструмента (FR-002) |
| `ticker` | `text` | `NULL` | может отсутствовать |
| `name` | `text` | `NULL` | человекочитаемое название; `NULL` не блокирует отображение |
| `asset_type` | `text` | `NULL` | акция / облигация / фонд / валюта |
| `currency` | `char(3)` | `NOT NULL` | |
| `quantity` | `numeric(28,9)` | `NOT NULL` | допускает отрицательные значения (короткая позиция) |
| `average_price` | `numeric(28,9)` | `NULL` | `NULL`, если брокер не дал цену приобретения |
| `current_price` | `numeric(28,9)` | `NOT NULL` | чистая цена без НКД |
| `accrued_interest` | `numeric(28,9)` | `NOT NULL DEFAULT 0` | НКД на одну облигацию; включён в `value`, хранится отдельно, чтобы разницу с «количество × цена» можно было объяснить |
| `value` | `numeric(28,9)` | `NOT NULL` | стоимость позиции |
| `unrealized_pnl` | `numeric(28,9)` | `NULL` | `NULL`, если неизвестна `average_price` |
| `sort_order` | `integer` | `NOT NULL` | порядок из ответа брокера, для стабильности вывода |

**Индексы**: `idx_position_state` на `(state_id)`.

**Правила**

- Доля в портфеле не хранится: вычисляется как `value / total_value` при отдаче;
  при `total_value = 0` — `0` без деления (Edge Case спеки).
- Отрицательное `quantity` допустимо и не искажает суммарные показатели (Edge Case спеки).
- `ticker`/`name` = `NULL` → в API уходит `instrument_uid`, строка отображается (Edge Case).

---

## 4. `broker_sync_state`

Статус последней попытки синхронизации и статус доступа к брокеру (FR-009, FR-020, FR-024).

| Поле | Тип | Ограничения | Требование |
|---|---|---|---|
| `id` | `smallint` | PK, `CHECK (id = 1)` | singleton |
| `broker_status` | `text` | `NOT NULL`, enum ниже | FR-020, FR-024 |
| `last_attempt_at` | `timestamptz` | `NULL` | |
| `last_success_at` | `timestamptz` | `NULL` | время последней успешной синхронизации (FR-014) |
| `last_status` | `text` | `NOT NULL`, `'ok' \| 'failed'` | FR-009 |
| `failure_reason_code` | `text` | `NULL`, enum ниже | FR-028: код, не текст брокера |
| `failure_detail` | `text` | `NULL` | санитизированная диагностика (FR-030, SC-009) |
| `consecutive_failures` | `integer` | `NOT NULL DEFAULT 0` | диагностика |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | |

**`broker_status`**

| Значение | Смысл | Состояние UI |
|---|---|---|
| `connected` | токен задан и принят брокером | обычные состояния портфеля |
| `not_configured` | `TBANK_INVEST_READ_TOKEN` не задан или пуст | «Брокер не подключён» |
| `rejected` | брокер отклонил токен (отозван, истёк, нет прав) | «Брокер не подключён» + предложение проверить конфигурацию |

**`failure_reason_code`**

| Код | Причина |
|---|---|
| `broker_unavailable` | недоступность, таймаут, сетевая ошибка |
| `broker_rejected_token` | токен отклонён (переводит `broker_status` в `rejected`) |
| `rate_limited` | превышены лимиты запросов |
| `validation_failed` | ответ не прошёл валидацию (FR-004) |
| `internal_error` | прочее |

**Правила**

- Неуспешная попытка изменяет **только** эту таблицу; `account_state` и
  `portfolio_position` остаются нетронутыми (FR-008, US4 AS3).
- `failure_detail` проходит через фильтр секретов; значение токена сюда попасть не может.

---

## 5. `account_refresh_settings`

Настройка интервала автообновления (FR-031, FR-034).

| Поле | Тип | Ограничения | Требование |
|---|---|---|---|
| `id` | `smallint` | PK, `CHECK (id = 1)` | singleton |
| `interval_seconds` | `integer` | `NOT NULL DEFAULT 60`, `CHECK (BETWEEN 15 AND 3600)` | FR-031 |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | |

**Правила**

- Диапазон продублирован в БД, Pydantic-схеме и UI: недопустимое значение отклоняется на
  каждом уровне (US2 AS4).
- Строка создаётся первой миграцией со значением по умолчанию 60 — система работоспособна
  без единого обращения пользователя к настройке.
- Порог устаревания (FR-040) не хранится: вычисляется как
  `max(3 × interval_seconds, 180)` при отдаче.

---

## 6. Инструменты: почему нет отдельной таблицы

Сущность «Инструмент» спеки денормализована в `portfolio_position`
(`instrument_uid`, `ticker`, `name`, `asset_type`, `currency`).

**Обоснование (Принцип II)**: в объёме фичи справочник инструментов не переиспользуется —
ни одного требования, читающего инструмент вне контекста позиции, нет. Отдельная таблица
с синхронизацией справочника — абстракция под будущие требования (котировки, история,
ранжирование Daily ML). Когда такие требования появятся, `instrument_uid` уже является
готовым внешним ключом, и выделение справочника не потребует переписывания позиций.

---

## 7. Транзакция успешной синхронизации

```text
BEGIN
  UPSERT investment_account            (broker_account_id, masked_id, display_name, currency)
  DELETE portfolio_position WHERE state_id = 1
  UPSERT account_state                 (captured_at, total_value, cash, cost_basis, pnl, count)
  INSERT portfolio_position            × N
  UPDATE broker_sync_state             (last_status='ok', last_success_at, last_attempt_at,
                                        broker_status='connected', failure_* = NULL,
                                        consecutive_failures = 0)
COMMIT
```

Транзакция неделима (FR-008): либо новое состояние заменяет прежнее целиком, либо не
меняется ничего.

## 8. Транзакция неуспешной синхронизации

```text
BEGIN
  UPDATE broker_sync_state (last_status='failed', last_attempt_at,
                            failure_reason_code, failure_detail,
                            consecutive_failures = consecutive_failures + 1,
                            broker_status = 'rejected' если код = broker_rejected_token)
COMMIT
```

`account_state` и `portfolio_position` не затрагиваются.

## 9. Вычисляемые в домене величины

| Величина | Формула | Граничный случай |
|---|---|---|
| Стоимость позиции | `quantity × (current_price + accrued_interest)` | у необлигаций НКД равен нулю |
| Доля позиции | `value / total_value` | `total_value = 0` → `0` |
| Доля денежных средств | `cash / total_value` | `total_value = 0` → `0` |
| % P&L портфеля | `unrealized_pnl / positions_cost_basis` | база `0` → `null` |
| % P&L позиции | `pnl / (average_price × quantity)` | `average_price` `NULL` или база `0` → `null` |
| Возраст данных | `now() − captured_at` | нет снимка → `null` |
| `is_stale` | `возраст > max(3 × interval, 180 c)` | нет снимка → `false`, состояние «нет данных» |

Округление до копеек выполняется **только там, где этого требует представление или
бизнес-правило**; в БД и в ответах API сохраняется исходная точность до 10⁻⁹
(SC-002, SC-003). Для отображаемых значений округление выполняет frontend при
форматировании под русскую локаль (FR-016).
