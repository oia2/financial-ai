# Contract: Backend-Worker (внутренний REST)

**База**: `http://backend-worker:8000/internal` | **Доступ**: только из сети docker compose

Nginx **не проксирует** этот контур наружу. Единственный клиент — `Backend-API`.
Это единственный сервис, которому передаётся `TBANK_INVEST_READ_TOKEN`.

---

## `POST /internal/sync`

Запускает `sync_account_state()` — ту же функцию, которую вызывает фоновый цикл
(FR-006: один код для автоматического и ручного обновления).

Тело запроса — пустое.

**Поведение при конкуренции** (FR-029, FR-033):

1. Если синхронизация уже выполняется, обращения к брокеру **не создаётся**: запрос
   ожидает завершения текущей и возвращает её результат с `deduplicated: true`.
2. Ожидание ограничено таймаутом; при его истечении возвращается
   `status: "failed"`, `failure_reason_code: "internal_error"`.

**200 OK**

```json
{
  "status": "ok",
  "deduplicated": false,
  "captured_at": "2026-08-26T11:35:02Z",
  "failure_reason_code": null,
  "duration_ms": 640
}
```

| Поле | Тип | Смысл |
|---|---|---|
| `status` | `ok` \| `failed` | исход обращения к брокеру |
| `deduplicated` | boolean | результат взят от уже выполнявшейся синхронизации |
| `captured_at` | string \| `null` | момент состояния при `status = "ok"` |
| `failure_reason_code` | string \| `null` | `broker_unavailable`, `broker_rejected_token`, `rate_limited`, `validation_failed`, `internal_error` |
| `duration_ms` | integer | для диагностики и проверки SC-004 |

Неуспех брокера — это **200 с `status: "failed"`**, а не 5xx: обращение к Worker состоялось,
и его результат описывает исход. 5xx от Worker означает отказ самого Worker и
транслируется `Backend-API` как `503 worker_unavailable`.

Сохранённое состояние при `status: "failed"` не изменяется (FR-008): обновляется только
`broker_sync_state` (см. [data-model.md §8](../data-model.md)).

---

## `GET /internal/health`

**200 OK**

```json
{
  "status": "ok",
  "database": "ok",
  "scheduler": "running",
  "broker_token": "configured",
  "current_interval_seconds": 60
}
```

| Поле | Значения |
|---|---|
| `scheduler` | `running` \| `stopped` |
| `broker_token` | `configured` \| `missing` — **факт наличия, никогда не значение** (FR-023, SC-009) |
| `current_interval_seconds` | интервал, применённый к текущему циклу |

**503** — БД недоступна или цикл синхронизации не запущен.

---

## Фоновый цикл (не HTTP, описан здесь как часть контракта Worker)

```text
lifespan startup → запустить задачу
цикл:
  interval = SELECT interval_seconds FROM account_refresh_settings   # каждый цикл
  под локом: sync_account_state()
  ждать interval секунд или сигнал остановки
lifespan shutdown → сигнал остановки, дождаться завершения текущей синхронизации
```

**Свойства, обеспечиваемые этой конструкцией**

| Требование | Как обеспечивается |
|---|---|
| FR-031 / SC-012 — новый интервал без перезапуска | интервал перечитывается из БД в начале каждого цикла |
| FR-032 — цикл не прекращается после ошибки | исключения синхронизации логируются и не выходят из цикла |
| FR-033 — нет наложения и накопления пропущенных интервалов | следующая итерация не начинается до завершения предыдущей; очереди нет |
| FR-029 — нет дублирующих обращений | общий `asyncio.Lock` + PostgreSQL advisory lock с ручной синхронизацией |

Корректное завершение обязательно: `shutdown` дожидается текущей синхронизации, чтобы
транзакция не оборвалась на середине.
