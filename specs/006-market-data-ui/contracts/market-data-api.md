# Contract: публичные маршруты рыночных данных

Публичная граница интерфейса. `backend-api` передаёт все четыре операции внутреннему
каналу `backend-worker` и **не считает полноту сам**: окно сбора задаётся конфигурацией
worker, и второй счёт разошёлся бы с фактическим окном (research.md, R1).

Образец взят у существующего `POST /api/portfolio/refresh`
(`backend/src/financial_ai/api/routes/portfolio.py`): тот же клиент, тот же таймаут
`worker_sync_timeout_seconds`, та же форма ошибки `{"detail": {"code", "message"}}` и тот
же перевод недоступности worker в `503 worker_unavailable`.

**Аутентификации у этих маршрутов нет** — как и у остальных публичных маршрутов установки
(решение Q2 спецификации). Доступ определяется развёртыванием.

Внутренний интерфейс worker остаётся тем же, что описан в
`specs/005-market-data-control/contracts/`, и наружу не проксируется.

---

## `GET /api/market-data/coverage`

Сводка полноты по группам источников. Ответы не кэшируются (`Cache-Control: no-store`):
возраст данных должен быть честным.

### `200 OK`

```json
{
  "asof_date": "2026-09-03",
  "catchup_window": {
    "date_from": "2026-04-20",
    "date_till": "2026-09-03",
    "sessions": 314
  },
  "groups": [
    {
      "group": "quotes",
      "title": "котировки",
      "has_history": true,
      "window_sessions": 314,
      "sessions_covered": 255,
      "coverage_ratio": 0.812,
      "period_from": "2025-06-10",
      "period_till": "2026-09-03",
      "gaps": 59,
      "rows_total": null,
      "rows_with_values": null,
      "value_ratio": 0.961,
      "looks_collected_but_empty": false
    },
    {
      "group": "positions",
      "title": "позиции по фьючерсам",
      "has_history": true,
      "window_sessions": 82,
      "sessions_covered": 82,
      "coverage_ratio": 1.0,
      "period_from": "2026-05-08",
      "period_till": "2026-09-02",
      "gaps": 0,
      "rows_total": 21320,
      "rows_with_values": 5,
      "value_ratio": 0.0002,
      "looks_collected_but_empty": true
    },
    {
      "group": "reference",
      "title": "справочники",
      "has_history": false,
      "rows_total": 506,
      "rows_with_values": 506,
      "value_ratio": 1.0,
      "looks_collected_but_empty": false
    }
  ]
}
```

| Правило | Почему |
|---|---|
| У группы без истории полей `window_sessions`, `sessions_covered`, `coverage_ratio`, `period_*`, `gaps` **нет вовсе** | Ноль читался бы как «ничего не собрано» (FR-014) |
| `rows_total` и `rows_with_values` могут быть `null` | Интерфейс показывает прочерк, а не ноль (FR-015) |
| `looks_collected_but_empty` считает сервер | Порог не должен жить второй жизнью в TypeScript (research.md, R2.1) |
| `catchup_window` описывает окно **догона**, а не окно группы | Окна групп различаются: 314 у котировок, 82 у позиций (research.md, R2.2) |

### `422 Unprocessable Entity` — календарь пуст

```json
{ "detail": { "code": "calendar_empty", "message": "календарь пуст: сначала выполните сбор" } }
```

Интерфейс показывает состояние «Хранилище пока пусто» и делает запуск догона недоступным
(FR-026).

### `503 Service Unavailable` — сборщик недоступен

```json
{ "detail": { "code": "worker_unavailable", "message": "сборщик данных недоступен" } }
```

Это **не** отказ операции: интерфейс помечает показанные ранее значения как последние
известные и не выдаёт их за подтверждённый процесс (FR-042, FR-047b).

---

## `GET /api/market-data/catchup`

Состояние прогона. Читается при входе в раздел, при возврате к нему и периодически, пока
прогон активен.

### `200 OK`

```json
{
  "status": "running",
  "groups": ["quotes", "aggregates", "global", "positions", "reference"],
  "date_from": "2026-04-20",
  "date_till": "2026-09-03",
  "clamped": false,
  "requested": 90,
  "closed": 18,
  "failed": 1,
  "remaining": 71,
  "current": "2026-05-14",
  "started_at": "2026-09-10T09:12:04+00:00",
  "finished_at": null,
  "reason": null
}
```

Поля повторяют `CatchupState.snapshot()` фичи 005 без изменений, кроме `reason`: он
приходит человекочитаемым (research.md, R2.3).

| Правило | Почему |
|---|---|
| `requested` берётся отсюда | Суммой пропусков по группам его вычислять нельзя (FR-029) |
| `remaining = requested − closed − failed` | `failed` в закрытые не входит (FR-030) |
| После перезапуска worker приходит `idle` | Состояние живёт в процессе; зависшего «идёт» не бывает по устройству |

### `503` — сборщик недоступен

Та же форма, что у сводки. Ранее показанное состояние не превращается в подтверждённый
идущий процесс (FR-036).

---

## `POST /api/market-data/catchup`

Запуск. Всё тело необязательно.

```json
{
  "groups": ["quotes", "aggregates"],
  "date_from": "2026-06-01",
  "date_till": "2026-08-28"
}
```

| Поле | Обязательно | Умолчание |
|---|---|---|
| `groups` | нет | все группы |
| `date_from` | нет | начало окна |
| `date_till` | нет | конец окна |

### `200 OK` — запуск принят

```json
{
  "status": "running",
  "groups": ["quotes", "aggregates"],
  "date_from": "2026-06-01",
  "date_till": "2026-08-28",
  "clamped": false,
  "requested_sessions": 42
}
```

При `clamped = true` интерфейс показывает рядом введённый и принятый диапазоны, и это
сообщение не исчезает по таймеру (FR-040).

### `200 OK` — догонять нечего

```json
{ "status": "idle", "requested_sessions": 0, "reason": "пропущенных сессий нет" }
```

Не ошибка: спокойное подтверждение полноты, новый прогон не предлагается (FR-038).

### Отказы

| Код HTTP | `code` | Что показывает интерфейс |
|---|---|---|
| `409` | `catchup_already_running` | Причину отказа; счётчики идущего прогона сохраняются (FR-039) |
| `422` | `backfill_required` | Объяснение про первичную загрузку и обращение к администратору; запуск недоступен (FR-026) |
| `422` | `unknown_group` | Отказ по устаревшему или некорректному запросу: в форме доступны только пять разрешённых групп |
| `422` | `invalid_range` | Ошибку у поля начала диапазона (FR-041) |
| `503` | `worker_unavailable` | Отсутствие связи со сборщиком, а не отказ операции |

---

## `DELETE /api/market-data/catchup`

Мягкая остановка: текущая сессия доводится до конца, дальнейшие не начинаются.

### `200 OK`

```json
{ "status": "stopping", "current": "2026-05-14" }
```

| Правило | Почему |
|---|---|
| Ответ не переводит интерфейс в `stopped` | Переход в остановленное состояние — только по подтверждённому состоянию прогона (FR-032) |
| Повторная остановка не предлагается | Запрос уже доведёт текущую сессию до конца (FR-024) |
| Остановка выполняется без дополнительного подтверждения | Операция безопасна: наполовину собранной сессии не остаётся (FR-023) |

Если прогон не активен, worker возвращает текущее состояние без изменений — отдельной
ошибки на это нет.
