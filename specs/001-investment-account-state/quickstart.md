# Quickstart: Investment Account State

Как поднять фичу локально и проверить, что она действительно работает. Документ —
руководство по запуску и валидации, а не по реализации: детали контрактов в
[contracts/](./contracts/), схема — в [data-model.md](./data-model.md).

---

## Предпосылки

| Что | Версия | Зачем |
|---|---|---|
| Docker + Docker Compose | актуальная | все контейнеры |
| Python | 3.12 | локальный запуск backend и тестов |
| `uv` | актуальная | зависимости backend |
| Node | 22 LTS | frontend |
| `pnpm` | актуальная | зависимости frontend |
| Токен Т-Банк Invest | read-only | доступ к T-Invest API |
| Доступ к `opensource.tbank.ru` | — | установка SDK, **на публичном PyPI его нет** |

Токен создаётся в личном кабинете Т-Инвестиций с правами **только на чтение**: фича не
инициирует торговых операций (FR-005), и токен с торговыми правами нарушает это
ограничение на уровне конфигурации.

---

## 1. Конфигурация

```bash
cp deployments/docker-compose/.env.example deployments/docker-compose/.env
```

Заполнить в `deployments/docker-compose/.env`:

```env
TBANK_INVEST_READ_TOKEN=<токен с правами только на чтение>
POSTGRES_PASSWORD=<локальный пароль>
```

Проверки:

- `.env` не попадает в git (`.gitignore` покрывает `.env` и `.env.*`, кроме `.env.example`);
- `TBANK_INVEST_READ_TOKEN` передаётся **только** сервису `backend-worker` — убедиться,
  что его нет в `environment` у `backend-api` и `frontend` (FR-023).

---

## 2. Запуск

```bash
docker compose -f deployments/docker-compose/docker-compose.yml up --build
```

Поднимутся `postgres`, `backend-api`, `backend-worker`, `frontend`.
Отдельного сервиса `nginx` нет: nginx работает внутри контейнера `frontend` — отдаёт
статику и проксирует `/api` на `backend-api`. Его конфиг —
`deployments/docker-compose/nginx/nginx.conf`, он монтируется в контейнер, поэтому
правится без пересборки образа.

Миграции Alembic применяются при старте `backend-api`.

Раздел «Портфель»: <http://localhost:8080>

Проверка живости:

```bash
curl -s localhost:8080/api/health
docker compose -f deployments/docker-compose/docker-compose.yml exec backend-worker \
  curl -s localhost:8000/internal/health
```

В ответе Worker поле `broker_token` должно быть `"configured"` — и никогда не содержать
самого значения токена.

---

## 3. Разработка без Docker

```bash
# backend
cd backend
uv sync                     # ставит t-tech-investments с индекса T-Bank
uv run alembic upgrade head
uv run uvicorn financial_ai.api.app:app --port 8001 --reload
uv run uvicorn financial_ai.worker.app:app --port 8000 --reload

# frontend
cd frontend
pnpm install
pnpm dev                    # проксирует /api на localhost:8001
```

PostgreSQL при этом удобно поднять одним сервисом:

```bash
docker compose -f deployments/docker-compose/docker-compose.yml up postgres
```

---

## 4. Проверочные сценарии

Соответствуют acceptance scenarios и Success Criteria спецификации.

### 4.1 Просмотр состояния счёта (US1, SC-002, SC-003)

1. Открыть <http://localhost:8080>.
2. Дождаться первой синхронизации (до одного интервала, по умолчанию 60 с).
3. Сверить показанное с ответом брокера:

```bash
curl -s localhost:8080/api/portfolio | python -m json.tool
```

**Ожидается**: общая стоимость, денежные средства с долей, P&L в рублях и процентах,
количество позиций, таблица позиций, возраст данных и время последней синхронизации.
Сумма стоимостей позиций и денежных средств равна общей стоимости; сумма долей — 1.

### 4.2 Автообновление (US2, SC-010)

1. Оставить раздел открытым, не выполняя действий.
2. Наблюдать за отметкой последней синхронизации.

**Ожидается**: отметка обновляется не реже раза за настроенный интервал; выбранная
сортировка таблицы и позиция прокрутки не сбрасываются.

### 4.3 Смена интервала (US2 AS3/AS4, SC-012)

```bash
curl -s -X PUT localhost:8080/api/settings/refresh-interval \
  -H 'Content-Type: application/json' -d '{"interval_seconds": 20}'

curl -s -X PUT localhost:8080/api/settings/refresh-interval \
  -H 'Content-Type: application/json' -d '{"interval_seconds": 5}'   # ожидается 422
```

**Ожидается**: допустимое значение применяется к следующему циклу без перезапуска
контейнеров; недопустимое отклоняется с кодом `interval_out_of_range`, прежний интервал
продолжает действовать.

### 4.4 Ручное обновление и дедупликация (US3, SC-004)

```bash
curl -s -X POST localhost:8080/api/portfolio/refresh          # ожидается ok, ≤ 5 с
curl -s -X POST localhost:8080/api/portfolio/refresh &        # два одновременных запроса
curl -s -X POST localhost:8080/api/portfolio/refresh &
wait
```

**Ожидается**: у одного из параллельных запросов `deduplicated: true`; в логах Worker —
ровно одно обращение к брокеру.

### 4.5 Сбой T-Bank API (US4 AS1, SC-005)

Смоделировать недоступность брокера:

```bash
docker compose -f deployments/docker-compose/docker-compose.yml exec backend-worker \
  sh -c 'echo "127.0.0.1 invest-public-api.tbank.ru" >> /etc/hosts'
```

**Ожидается**: отображается последнее известное состояние с баннером «Не удалось обновить
портфель», указано время последней успешной синхронизации, доступен повтор. Значения в
таблице **не** обнуляются, `GET /api/portfolio` возвращает прежние данные с
`sync.status: "failed"`.

### 4.6 Обрыв связи с сервером (US4 AS6/AS7, SC-011)

При открытом разделе:

```bash
docker compose -f deployments/docker-compose/docker-compose.yml stop backend-api
# ...проверить UI...
docker compose -f deployments/docker-compose/docker-compose.yml start backend-api
```

**Ожидается**: баннер «Нет связи с сервером» — **другой** по формулировке, чем в 4.5;
данные помечены как последние известные с возрастом; после запуска `backend-api` баннер
исчезает сам, без перезагрузки страницы.

Оба сценария, 4.5 и 4.6, должны давать различимые сообщения — это и есть проверка
SC-011.

### 4.7 Брокер не подключён (FR-020, FR-024)

Убрать `TBANK_INVEST_READ_TOKEN` из окружения Worker и перезапустить его.

**Ожидается**: состояние «Брокер не подключён» с указанием, что доступ не сконфигурирован;
`broker.status` = `not_configured`; значение токена нигде не выводится.

### 4.8 Устаревание данных (US4 AS2, FR-040)

Остановить `backend-worker` при наличии сохранённого состояния и подождать
`max(3 × интервал, 180 с)`.

**Ожидается**: предупреждение об устаревших данных с указанием возраста; после запуска
Worker и успешной синхронизации предупреждение исчезает (US4 AS5).

### 4.9 Сортировка позиций (US5)

**Ожидается**: сортировка по любому столбцу, повторный клик меняет направление,
по умолчанию — убывание доли в портфеле.

---

## 5. Проверки качества (Принцип IV Constitution)

```bash
scripts/check.sh                # полный гейт, включая сборку образов
scripts/check.sh --no-docker    # быстрый цикл разработки, НЕ полный гейт
```

Скрипт прогоняет: `ruff check`, `ruff format --check`, `mypy`, `pytest`,
`alembic upgrade head` на свежесозданной БД, `eslint`, `prettier --check`,
`tsc --noEmit`, `vitest` и `docker compose build`.

Запускать проверки по отдельности не нужно и не следует: скрипт существует
именно потому, что частичные прогоны уже пропускали дефекты — `ruff` по
`src/` и `tests/` не видел `migrations/`, а неверный тег базового образа
ловится только сборкой.

Фича не считается завершённой, пока `scripts/check.sh` не проходит целиком.

---

## 6. Проверка безопасности перед сдачей (Принцип VII, SC-009)

```bash
# токена нет ни в одном ответе API и ни в одном логе
docker compose -f deployments/docker-compose/docker-compose.yml logs \
  | grep -c "$(grep TBANK_INVEST_READ_TOKEN deployments/docker-compose/.env | cut -d= -f2)"
# ожидается: 0

# токен не отслеживается git
git ls-files | grep -E '(^|/)\.env$'    # ожидается: пусто
```

Дополнительно убедиться, что в ответах API встречается только маскированный номер
договора (`•• 4821`) и ни разу — полный (FR-022).
