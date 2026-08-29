# Quickstart: Эмулятор Daily ML

Как поднять эмулятор и убедиться, что он действительно работает. Документ — руководство по
запуску и проверке, а не по реализации: контракт эндпоинтов —
в [contracts/daily-ml-emulator-api.md](./contracts/daily-ml-emulator-api.md), структуры
данных и правило ранжирования — в [data-model.md](./data-model.md).

> Эмулятор изображает работу модели: все скоры вымышлены. Ни модели, ни рыночных данных,
> ни вычислений за ними нет.

---

## Предпосылки

| Что | Версия | Зачем |
|---|---|---|
| Docker + Docker Compose | актуальная | запуск контейнера |
| Python | 3.12 | локальный запуск и тесты |
| `uv` | актуальная | зависимости эмулятора |

Ни токена, ни базы данных, ни доступа в интернет эмулятору не требуется — в отличие от
остальных сервисов проекта.

---

## 1. Запуск в контейнере

Эмулятор — сервис `daily-ml-emulator` в общем compose-файле проекта. Его можно поднять
одним сервисом, не запуская всю систему:

```bash
docker compose -f deployments/docker-compose/docker-compose.yml up --build daily-ml-emulator
```

Порт наружу — `DAILY_ML_EMULATOR_PORT`, по умолчанию `8100`. Значение при необходимости
задаётся в `deployments/docker-compose/.env`; секретов среди переменных эмулятора нет.

Проверка готовности:

```bash
curl -s localhost:8100/health
```

Ожидается:

```json
{"status":"ok"}
```

---

## 2. Первое ранжирование

```bash
curl -s "localhost:8100/rankings?decision_date=2026-08-28"
```

Ожидается ответ вида (сокращён до трёх позиций из десяти):

```json
{
  "decision_date": "2026-08-28",
  "model_id": "daily-ml-emulator-v1",
  "generated_at": "2026-08-29T09:14:02Z",
  "emulated": true,
  "emulation_notice": "Значения вымышлены эмулятором Daily ML. Модели, рыночных данных и вычислений за ними нет.",
  "items": [
    { "rank": 1, "asset_id": "EQ_AST_ROSN", "price_series_id": "EQ_PRS_ROSN", "score": "0.9091" },
    { "rank": 2, "asset_id": "EQ_AST_NVTK", "price_series_id": "EQ_PRS_NVTK", "score": "0.8182" },
    { "rank": 3, "asset_id": "EQ_AST_TATN", "price_series_id": "EQ_PRS_TATN", "score": "0.7273" }
  ]
}
```

---

## 3. Документация эндпоинтов от самого сервиса

Запущенный эмулятор сам отдаёт схему своих эндпоинтов:

- <http://localhost:8100/docs> — страница документации;
- <http://localhost:8100/openapi.json> — машиночитаемая схема.

---

## Проверочные сценарии

Сценарии соответствуют критериям приёмки из [spec.md](./spec.md). Каждый выполняется
командами выше, без чтения исходного кода.

### С1. Ранжирование содержит всю вселенную (US1 AS1, FR-002, FR-004)

```bash
curl -s "localhost:8100/rankings?decision_date=2026-08-28" \
  | python -c "import json,sys; d=json.load(sys.stdin); r=[i['rank'] for i in d['items']]; print('позиций:', len(r), '| ранги 1..N:', r == list(range(1, len(r)+1)))"
```

Ожидается: `позиций: 10 | ранги 1..N: True`

### С2. Один и тот же запрос даёт один и тот же ответ (US1 AS2, FR-009, SC-003)

```bash
A=$(curl -s "localhost:8100/rankings?decision_date=2026-08-28" | python -c "import json,sys; print(json.load(sys.stdin)['items'])")
B=$(curl -s "localhost:8100/rankings?decision_date=2026-08-28" | python -c "import json,sys; print(json.load(sys.stdin)['items'])")
[ "$A" = "$B" ] && echo "детерминизм: OK" || echo "детерминизм: ПРОВАЛ"
```

Сравнивается состав `items`. Поле `generated_at` намеренно исключено — это момент
формирования ответа, он обязан отличаться.

Проверка «в том числе после перезапуска»: повторить после
`docker compose -f deployments/docker-compose/docker-compose.yml restart daily-ml-emulator`.

### С3. Разные даты дают разный порядок (US1 AS3, FR-010)

```bash
for D in 2026-08-28 2026-08-29 2026-08-31; do
  echo -n "$D → "
  curl -s "localhost:8100/rankings?decision_date=$D" \
    | python -c "import json,sys; print(json.load(sys.stdin)['items'][0]['asset_id'])"
done
```

Ожидается три разных актива на первом месте:

```text
2026-08-28 → EQ_AST_ROSN
2026-08-29 → EQ_AST_GMKN
2026-08-31 → EQ_AST_GAZP
```

### С4. Некорректная дата отвергается (US1 AS4, FR-013)

```bash
curl -s -o /dev/null -w "%{http_code}\n" "localhost:8100/rankings?decision_date=28.08.2026"
curl -s -o /dev/null -w "%{http_code}\n" "localhost:8100/rankings"
```

Ожидается `422` в обоих случаях, ранжирование не выдаётся.

### С5. Ответ помечен как эмуляция (US1 AS5, FR-006, SC-005)

```bash
curl -s "localhost:8100/rankings?decision_date=2026-08-28" \
  | python -c "import json,sys; print('emulated =', json.load(sys.stdin)['emulated'])"
```

Ожидается: `emulated = True`

### С6. Вселенная настраивается без пересборки образа (US4, FR-016)

Подготовить свой файл вселенной:

```bash
cat > /tmp/universe.json <<'EOF'
[
  { "asset_id": "EQ_AST_SBER", "price_series_id": "EQ_PRS_SBER" },
  { "asset_id": "EQ_AST_GAZP", "price_series_id": "EQ_PRS_GAZP" }
]
EOF
```

Запустить контейнер с ним, смонтировав файл поверх файла по умолчанию:

```bash
docker run --rm -p 8101:8000 \
  -v /tmp/universe.json:/app/universe/default.json:ro \
  financial-ai-daily-ml-emulator:latest
```

Ожидается: ответ на `localhost:8101/rankings?decision_date=2026-08-28` содержит ровно два
актива с рангами 1 и 2 и скорами `0.6667` и `0.3333` (лестница при N = 2).

> **В Git Bash на Windows** путь `/tmp/...` слева от двоеточия преобразуется в
> windows-путь, и монтирование молча не срабатывает: контейнер поднимется на встроенной
> вселенной, а проверка покажет 10 активов вместо двух. Использовать абсолютный
> windows-путь с прямыми слэшами и `MSYS_NO_PATHCONV=1`:
>
> ```bash
> MSYS_NO_PATHCONV=1 docker run --rm -p 8101:8000 \
>   -v "C:/путь/к/universe.json:/app/universe/default.json:ro" \
>   financial-ai-daily-ml-emulator:latest
> ```

### С7. Непригодная конфигурация не даёт контейнеру подняться (FR-017)

```bash
echo '[]' > /tmp/empty-universe.json
docker run --rm \
  -v /tmp/empty-universe.json:/app/universe/default.json:ro \
  financial-ai-daily-ml-emulator:latest; echo "код выхода: $?"
```

Ожидается код выхода `3` и в логе:

```text
UniverseError: вселенная в universe/default.json пуста: ранжировать нечего.
ERROR:    Application startup failed. Exiting.
```

То же — для файла с дублирующимся `asset_id`; сообщение назовёт конкретный
идентификатор и номер записи. Оговорка про Git Bash из сценария С6 действует и здесь.

### С8. Секреты и внешняя сеть не нужны (FR-018, SC-006)

Убедиться, что в описании сервиса `daily-ml-emulator` в `docker-compose.yml` нет ни
`TBANK_INVEST_READ_TOKEN`, ни `DATABASE_URL`, ни `depends_on`:

```bash
python - <<'PY'
import re, pathlib
text = pathlib.Path("deployments/docker-compose/docker-compose.yml").read_text(encoding="utf-8")
block = re.search(r"\n  daily-ml-emulator:\n(.*?)(?=\n  \w|\nvolumes:)", text, re.S)
body = block.group(1) if block else ""
for token in ("TBANK_INVEST_READ_TOKEN", "DATABASE_URL", "depends_on"):
    print(f"{token}: {'НАЙДЕНО — ПРОВАЛ' if token in body else 'отсутствует — OK'}")
PY
```

---

## Локальная разработка без контейнера

```bash
cd daily-ml-emulator
uv sync
uv run uvicorn daily_ml_emulator.app:app --port 8100 --reload
```

Вселенная по умолчанию берётся из `daily-ml-emulator/universe/default.json`; путь
переопределяется переменной `DAILY_ML_EMULATOR_UNIVERSE_PATH`.

---

## Проверки качества

Фича не считается завершённой, пока не проходит общий гейт проекта — он включает проверки
эмулятора и сборку его образа:

```bash
scripts/check.sh                # полный гейт, включая сборку образов
scripts/check.sh --no-docker    # быстрый цикл разработки, НЕ полный гейт
```

Запускать проверки по частям не следует: именно частичные прогоны в этом проекте уже
пропускали дефекты.

Только тесты эмулятора, для быстрого цикла:

```bash
cd daily-ml-emulator && uv run pytest -q
```
