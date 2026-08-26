# Contract: Backend-API (публичный)

**База**: `/api` (проксируется Nginx) | **Формат**: JSON, UTF-8 | **Время**: ISO 8601, UTC

**Точность чисел**: все денежные, ценовые и количественные значения передаются
**строками** (`"362068.000000000"`), чтобы не терять точность в JavaScript
(SC-002, SC-003 — см. [research.md §5](../research.md)). Форматирование под русскую
локаль выполняет frontend (FR-016).

---

## `GET /api/portfolio`

Полное состояние раздела «Портфель» одним ответом: данные, статус подключения брокера и
статус синхронизации. Frontend ничего из этого не вычисляет сам (FR-015, FR-037).

**200 OK**

```json
{
  "broker": {
    "status": "connected",
    "account": {
      "display_name": "Основной брокерский счёт",
      "masked_id": "•• 4821",
      "currency": "RUB"
    }
  },
  "snapshot": {
    "captured_at": "2026-08-26T11:32:18Z",
    "age_seconds": 42,
    "total_value": "402613.000000000",
    "cash": "40545.000000000",
    "cash_share": "0.100704",
    "unrealized_pnl": "4590.000000000",
    "unrealized_pnl_percent": "0.0129",
    "positions_count": 5,
    "positions": [
      {
        "instrument_uid": "e6123145-9665-43e0-8413-cd61b8aa9b13",
        "ticker": "SBER",
        "name": "Сбербанк",
        "asset_type": "share",
        "currency": "RUB",
        "quantity": "1200.000000000",
        "average_price": "281.400000000",
        "current_price": "301.720000000",
        "accrued_interest": "0.000000000",
        "value": "362064.000000000",
        "unrealized_pnl": "24384.000000000",
        "unrealized_pnl_percent": "0.0722",
        "share": "0.899287"
      }
    ]
  },
  "sync": {
    "status": "ok",
    "last_success_at": "2026-08-26T11:32:18Z",
    "last_attempt_at": "2026-08-26T11:32:18Z",
    "failure_reason_code": null,
    "is_stale": false,
    "stale_after_seconds": 180,
    "refresh_interval_seconds": 60,
    "in_progress": false
  }
}
```

### Поля

| Поле | Тип | Смысл |
|---|---|---|
| `broker.status` | `connected` \| `not_configured` \| `rejected` | FR-020, FR-024 |
| `broker.account` | объект \| `null` | `null`, пока счёт ни разу не получен |
| `snapshot` | объект \| `null` | `null`, если успешной синхронизации ещё не было (US4 AS4) |
| `snapshot.age_seconds` | integer | возраст данных (FR-014) |
| `*_percent` | string \| `null` | доля единицы (`"0.0129"` = +1,29%); `null`, когда база нулевая |
| `accrued_interest` | string | Накопленный купонный доход на одну облигацию. **Уже включён в `value`**: брокер считает стоимость облигации как `quantity × (current_price + accrued_interest)`, и без него суммы расходятся с его итогом. У остальных инструментов — `"0"` |
| `share` | string | доля в портфеле, доля единицы; сумма долей позиций и `cash_share` = 1 (SC-003) |
| `sync.status` | `ok` \| `failed` | результат последней попытки (FR-009) |
| `sync.failure_reason_code` | см. [data-model.md §4](../data-model.md) \| `null` | код, а не текст брокера (FR-028) |
| `sync.is_stale` | boolean | вычислено backend по FR-040 |
| `sync.stale_after_seconds` | integer | `max(3 × интервал, 180)` |
| `sync.refresh_interval_seconds` | integer | действующий интервал; frontend строит из него свой poll-интервал (FR-036) |
| `sync.in_progress` | boolean | синхронизация выполняется прямо сейчас |

### Состояния UI, выводимые из ответа

| Состояние (FR-015) | Условие |
|---|---|
| Брокер не подключён | `broker.status != "connected"` |
| Портфель пуст | `snapshot != null`, `positions_count == 0`, `cash == "0"` |
| Данные актуальны | `sync.status == "ok"`, `is_stale == false` |
| Данные устарели | `is_stale == true` |
| Ошибка синхронизации | `sync.status == "failed"` |
| Загрузка | запрос выполняется |
| **Нет связи с сервером** | **запрос не завершился или 502/503/504** — тела ответа нет (FR-037, FR-038) |

Последняя строка — принципиальная: причина «нет связи с сервером» определяется
отсутствием ответа, а не полем внутри ответа. Одна причина не может быть выдана за
другую.

---

## `POST /api/portfolio/refresh`

Ручное обновление (FR-006, US3). Транслируется в `Backend-Worker`.

Тело запроса — пустое.

**200 OK** — синхронизация выполнена (успешно или нет; тело описывает исход):

```json
{
  "status": "ok",
  "deduplicated": false,
  "captured_at": "2026-08-26T11:35:02Z",
  "failure_reason_code": null
}
```

| Поле | Смысл |
|---|---|
| `status` | `ok` \| `failed` — результат обращения к брокеру |
| `deduplicated` | `true`, если синхронизация уже шла и запрос дождался её результата, не создавая второго обращения к брокеру (FR-029, US3 AS3) |
| `failure_reason_code` | код причины при `status = "failed"` |

**503 Service Unavailable** — `Backend-Worker` недоступен для `Backend-API`:

```json
{ "detail": { "code": "worker_unavailable" } }
```

Для frontend это внутренняя ошибка сервера, а не «нет связи с сервером»: ответ получен,
значит связь с Financial AI есть. Отображается как ошибка синхронизации.

**409 Conflict** не используется: конкурентный запрос не отклоняется, а дедуплицируется.

---

## `GET /api/settings/refresh-interval`

**200 OK**

```json
{
  "interval_seconds": 60,
  "min_seconds": 15,
  "max_seconds": 3600,
  "default_seconds": 60
}
```

Границы приходят с сервера, чтобы UI показывал допустимый диапазон, а не хардкодил его
(FR-035).

---

## `PUT /api/settings/refresh-interval`

```json
{ "interval_seconds": 120 }
```

**200 OK** — тело как у `GET`.

**422 Unprocessable Entity** — значение вне диапазона или не целое (US2 AS4):

```json
{
  "detail": {
    "code": "interval_out_of_range",
    "min_seconds": 15,
    "max_seconds": 3600
  }
}
```

Прежний интервал при этом продолжает действовать. Новое значение применяется к
**следующему** циклу фоновой синхронизации, без перезапуска (SC-012).

---

## `GET /api/health`

**200 OK** — `{"status": "ok", "database": "ok"}`; **503** при недоступной БД.

Используется Docker healthcheck и Nginx. UI его не опрашивает: отсутствие связи наблюдается
по основному запросу (research §7).

---

## Общие правила

1. Значение `TBANK_INVEST_READ_TOKEN` не появляется ни в одном ответе, включая ошибки
   (FR-023, SC-009). Полный номер договора не передаётся — только `masked_id` (FR-022).
2. Тексты ошибок брокера наружу не транслируются: наружу уходит только код
   `failure_reason_code`, пользовательскую формулировку строит frontend (FR-028).
3. Ответы не кэшируются (`Cache-Control: no-store`) — возраст данных должен быть честным.
4. `value` позиции не равен `quantity × current_price` у облигаций: в стоимость входит
   накопленный купонный доход. Клиент, пересчитывающий стоимость сам, обязан учитывать
   `accrued_interest`, иначе разойдётся и с суммами ответа, и с итогом брокера.
5. Ошибки отдаются в едином виде: `{"detail": {"code": "...", ...}}`.
