# Contract: состояние сбора и журнал прогонов

Публичная граница раздела «Рыночные данные». `backend-api` передаёт запросы внутреннему
интерфейсу `backend-worker` тем же способом, каким уже передаёт догон и синхронизацию.
Внутренний интерфейс наружу не выставляется.

Изменения этой фичи: состояние прогона получает план источников, причины пропусков и режим;
появляется журнал прогонов, который переживает перезапуск сборщика.

---

## `GET /api/market-data/catchup`

Состояние прогона. Не кэшируется.

```json
{
  "status": "running",
  "mode": "daily",
  "groups": ["quotes", "aggregates", "global", "positions", "reference"],
  "date_from": "2026-09-01",
  "date_till": "2026-09-16",
  "clamped": false,
  "sessions": {
    "requested": 12,
    "collected": 6,
    "partial": 1,
    "failed": 1,
    "skipped": 2,
    "pending": 2,
    "outcomes": [
      { "session_date": "2026-09-01", "outcome": "collected" },
      { "session_date": "2026-09-09", "outcome": "failed" },
      { "session_date": "2026-09-14", "outcome": "skipped" }
    ]
  },
  "skips": [
    {
      "session_date": "2026-09-14",
      "reason": "retry_delay",
      "detail": "следующая попытка через 12 минут"
    },
    {
      "session_date": "2026-09-15",
      "reason": "attempts_exhausted",
      "detail": "5 попыток из 5"
    }
  ],
  "current": {
    "session_date": "2026-09-11",
    "sources": [
      { "source_id": "trading_calendar", "title": "Торговый календарь", "scope": "daily", "state": "done" },
      { "source_id": "equity_d1", "title": "Котировки акций", "scope": "session", "state": "done", "rows": 243 },
      { "source_id": "index_constituents", "title": "Состав индекса", "scope": "session", "state": "running", "detail": "1 из 2 индексов" },
      { "source_id": "futures_positions", "title": "Позиции по фьючерсам", "scope": "session", "state": "pending" }
    ]
  },
  "started_at": "2026-09-17T14:58:00+00:00",
  "finished_at": null,
  "last_response_at": "2026-09-17T15:12:42+00:00",
  "stop_requested": false,
  "reason": null
}
```

**Поля, которых раньше не было и которые интерфейс не вправе вычислять сам:**

- `mode` — `daily` или `manual`. Состав плана у режимов **разный**, и число источников
  интерфейс не задаёт (FR-004).
- `sessions.outcomes` — исход каждой сессии плана (FR-001).
- `skips` — причина каждого пропуска из закрытого перечня: `withheld_until_close`,
  `retry_delay`, `attempts_exhausted`, `gap_over_limit` (FR-002).
- `current.sources` — план источников текущей сессии **в порядке выполнения** с состоянием
  каждого: `done`, `running`, `pending`, `failed`, `skipped` (FR-003).
- `scope` у источника — `session`, `period` или `daily`. Источники, которые идут раз на
  период или раз в сутки, в счётчик источников сессии не входят: иначе счётчик обещал бы,
  что они повторятся на следующий день (FR-007).

`status`: `idle`, `running`, `stopping`, `stopped`, `finished`, `failed`, `interrupted`.
Последнее — прогон, оборванный перезапуском сборщика (FR-041).

---

## `GET /api/market-data/runs`

Журнал последних прогонов. Читается из таблицы исходов сбора, поэтому переживает
перезапуск сборщика (FR-005).

```json
{
  "runs": [
    {
      "run_id": "0f1c…",
      "mode": "daily",
      "started_at": "2026-09-17T14:58:00+00:00",
      "finished_at": "2026-09-17T15:14:12+00:00",
      "status": "finished",
      "sessions": { "requested": 12, "collected": 10, "failed": 1, "skipped": 1 },
      "failures": [
        { "source_id": "brent", "session_date": "2026-09-09", "reason": "источник не ответил вовремя" }
      ]
    }
  ]
}
```

Параметр `limit` — не более 20, умолчание 5.

`failures[].reason` формулирует сервер. Адрес, по которому шло обращение, в ответ не
попадает: он может быть внутренним, и на экране раскрывал бы конфигурацию развёртывания.

---

## `POST /api/market-data/catchup`

Запуск ручного сбора. Без изменений формы запроса; ответ дополняется полем `mode`
со значением `manual`.

---

## `DELETE /api/market-data/catchup`

Мягкая остановка. Ответ означает, что остановка **запрошена**: текущая сессия доводится до
конца. Интерфейс переходит в состояние остановки по состоянию прогона, а не по факту
нажатия.

---

## Внутренний интерфейс `backend-worker`

| Маршрут | Назначение |
|---|---|
| `GET /internal/catchup` | состояние прогона, форма выше |
| `GET /internal/runs` | журнал прогонов, форма выше |
| `POST /internal/catchup` | запуск |
| `DELETE /internal/catchup` | остановка |

Ответ worker передаётся публичной границей как есть. `503` с телом
`{"code": "worker_unavailable"}` возникает только тогда, когда ответа не было вовсе:
недоступность сборщика и отказ операции — разные вещи.
