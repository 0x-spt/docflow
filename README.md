# DocFlow

API для распознавания документов: принимает фото или PDF, асинхронно возвращает PDF-скан с текстовым слоем,
данные, извлечённые по схеме документа (счёт, УПД, паспорт или своя схема), результат проверок реквизитов
(контрольные суммы ИНН/БИК/р/с, суммы по таблице) и вебхук. Статус `ok` — данным можно верить, `needs_review` —
нужна проверка человеком, `failed` — обработать не удалось. Внешние зависимости (модель, OCR, хранилище) скрыты
за протоколами, поэтому юнит-тесты идут без сети и Docker.

## Быстрый старт

```
export ANTHROPIC_API_KEY=sk-...   # передаётся в контейнеры; без ключа документы клиентов claude уйдут в failed
docker compose up -d --build      # postgres, redis, s3, migrate (миграции + схемы), api, worker
docker compose exec api docflow create-tenant "Ромашка"
# → API-ключ: dfk_...  (показывается один раз)
```

Загрузка, опрос, скачивание PDF:

```
KEY=dfk_...
curl -s -X POST http://localhost:8000/v1/documents \
  -H "Authorization: Bearer $KEY" -H "Idempotency-Key: $(uuidgen)" \
  -F schema=invoice_ru -F files=@schet.jpg            # несколько страниц — несколько -F files=@...
# → {"id": "…", "status": "queued"}

curl -s http://localhost:8000/v1/documents/<id> -H "Authorization: Bearer $KEY"
# → status: queued → processing → ok | needs_review | failed; data, issues, pdf_url

curl -s -o scan.pdf http://localhost:8000/v1/documents/<id>/pdf -H "Authorization: Bearer $KEY"
```

Повтор `POST` с тем же `Idempotency-Key` вернёт уже принятый документ. Документация API — http://localhost:8000/docs.

## Справочники

Клиники, врачи, контрагенты, склады — всё, что у клиента из фиксированного списка. Поле схемы с правилом
`{kind: dictionary, params: {name: clinics}}` сверяется со справочником `clinics` этого клиента:

- совпадение без учёта регистра, пробелов, пунктуации и ё/е → значение приводится к записи справочника;
- одна-две буквы не те («Дента-Локс») и кандидат единственный → исправляется, в `issues` — `corrected`
  (на статус не влияет);
- нет в справочнике или кандидатов несколько → `not_in_dictionary`, документ уходит на проверку. Выдуманное
  моделью название хуже пустого поля. `fuzzy: false` в правиле отключает автоисправление.

```
curl -s -X PUT http://localhost:8000/v1/dictionaries/clinics -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Клиники", "entries": [{"value": "Дента-Люкс"}, {"value": "Улыбка+", "aliases": ["Улыбка плюс"]}]}'
```

`PUT` заменяет справочник целиком (до 5000 записей), `GET /v1/dictionaries[/{name}]`, `DELETE /v1/dictionaries/{name}`.

## Доверие к модели (`auto_ok`)

Проверки ловят арифметику, контрольные суммы и даты, но не неверно прочитанную фамилию: сильная модель сама отмечает
сомнения (`_uncertain`), слабая ошибается уверенно. Поэтому у клиента есть флаг `auto_ok`: без него статус `ok` не
ставится, всё уходит в `needs_review` как черновик (`review_required`). По умолчанию — да для `claude`, нет для
`ollama`. Включать после эталонного прогона на документах клиента:

```
docker compose exec api docflow create-tenant "Лаборатория" --extractor ollama   # печатает id, auto_ok: нет
docker compose exec api docflow set-auto-ok <id> yes
```

## Сайт и кабинет

Сайт (`web/`, Next.js) — лендинг с формой заявки на аудит и кабинет клиента (очередь, проверка документов,
загрузка, справочники, ключи и вебхуки). Браузер токена API не видит: сессия — httpOnly-cookie `df_session`,
запросы к API идут с сервера сайта.

Запуск вместе со всем стеком:

```
docker compose up -d --build        # сайт: http://localhost:3000, API: http://localhost:8000 (только 127.0.0.1)
```

Клиент и его пользователь (пароль не короче 10 символов; без `--password` будет сгенерирован и показан один раз):

```
docker compose exec api docflow create-tenant "Моя компания"        # → id и API-ключ
docker compose exec api docflow create-user <tenant_id> boss@example.com --role owner --password '<пароль>'
```

Роли: `owner` (всё, включая ключи и вебхуки) и `operator` (очередь, проверка, загрузка). Вход — на `/login`.

Заявки на аудит из формы на лендинге сохраняются в БД: `docker compose exec api docflow leads`. Мгновенное
уведомление в Telegram включается переменными `DOCFLOW_LEADS_TG_TOKEN` (токен бота) и `DOCFLOW_LEADS_TG_CHAT`
(id чата); без них заявки просто лежат в БД.

В проде:

- сайт и API за обратным прокси с TLS; в `docker-compose.yml` порты 3000 и 8000 опубликованы только на
  `127.0.0.1`, наружу смотрит лишь прокси;
- cookie получает флаг `Secure` в production-сборке, если не задано `COOKIE_SECURE=false`. В compose по умолчанию
  `false` (dev по http) — в проде за https задайте в `.env` `COOKIE_SECURE=true`;
- прокси обязан сам выставлять `X-Real-IP` адресом соединения и не пропускать присланный клиентом: в nginx
  `proxy_set_header X-Real-IP $remote_addr;` (и `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`).
  Сайт берёт адрес посетителя из `X-Real-IP`, без него — из последнего адреса `X-Forwarded-For`; первому адресу не
  верит. Этот адрес он передаёт API для лимитов на заявки и вход, а API верит ему только с общим секретом
  `DOCFLOW_INTERNAL_TOKEN` — одинаковым у сайта и API. Значения по умолчанию нет: задайте случайное в `.env`
  (пусто — API не верит адресу от сайта, и все посетители делят один лимит по адресу контейнера сайта);
- загрузка документов идёт через сайт, поэтому лимит тела ~200 MB (`client_max_body_size 200m;`) ставьте только
  на `location /app/upload`, на остальных адресах оставьте малый;
- `DOCFLOW_API_URL` у сайта — внутренний адрес API, наружу порт 8000 в проде не публикуется.

Сквозные тесты сайта (Playwright, против стека с fake-экстрактором; подробности — `web/README.md`):

```
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
cd web && npm ci && npx playwright install chromium && npm run test:e2e
docker compose up -d        # вернуть обычный воркер
```

## Вебхуки

```
curl -s -X POST http://localhost:8000/v1/webhooks -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/hook", "events": ["document.completed", "document.failed", "document.reviewed"]}'
# → {..., "secret": "..."}  — секрет показывается один раз
```

События: `document.completed` — обработка закончилась (`ok` или `needs_review`), `document.failed` — не удалась
или отклонена оператором, `document.reviewed` — документ подтверждён человеком после проверки в кабинете (приходит
и подписчикам `document.completed`).

Тело — JSON `{"event": ..., "document": {...}}`, подпись — HMAC-SHA256 от сырых байтов тела в заголовке
`X-DocFlow-Signature: sha256=<hex>`. Проверка на Python:

```python
import hashlib
import hmac

def verify(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

# Flask: verify(SECRET, request.get_data(), request.headers["X-DocFlow-Signature"])
```

Проверяйте подпись по полученным байтам, а не по заново сериализованному JSON. Неуспешные доставки повторяются
через 10 с, 1 мин, 5 мин, 30 мин, 2 ч. Вебхуки на частные адреса запрещены (`DOCFLOW_WEBHOOK_ALLOW_PRIVATE=true`
— только для dev и e2e).

## Порты

| Сервис | На хосте | Внутри сети compose |
|---|---|---|
| Сайт | 127.0.0.1:3000 | `web:3000` |
| API | 127.0.0.1:8000 | `api:8000` |
| Postgres | **127.0.0.1:55432** (5432 оставлен локальному Postgres) | `postgres:5432` |
| Redis | 127.0.0.1:6379, с паролем | `redis:6379` |
| S3 (SeaweedFS) | 127.0.0.1:8333 | `s3:8333` |

API, сайт и хранилища опубликованы только на loopback. Секреты (`DOCFLOW_PG_USER`/`DOCFLOW_PG_PASSWORD`,
`DOCFLOW_REDIS_PASSWORD`, `DOCFLOW_S3_ACCESS_KEY`/`DOCFLOW_S3_SECRET_KEY`) compose берёт из окружения или `.env`
(образец — `.env.example`); без них подставляются dev-значения. Файл ключей SeaweedFS генерируется при старте
контейнера из `DOCFLOW_S3_*`.

## Лимиты

`DOCFLOW_MAX_FILE_MB` (20) — один файл, `DOCFLOW_MAX_PAGES` (30) — страниц в документе,
`DOCFLOW_MAX_REQUEST_MB` (200) — всё тело `POST /v1/documents`. Превышение отклоняется (413/422) до постановки
в очередь.

## Разработка и тесты

```
uv sync --all-extras
uv run pytest -m "not integration and not e2e"     # юнит-тесты: без сети и Docker
uv run ruff check
```

Интеграционные (нужен запущенный стек):

```
# с хоста — Postgres и S3 (OCR-тест пропускается, если нет tesseract)
DOCFLOW_DATABASE_URL=postgresql+asyncpg://docflow:docflow@127.0.0.1:55432/docflow \
DOCFLOW_S3_ENDPOINT=http://127.0.0.1:8333 uv run pytest -m integration
# все, включая OCR, — внутри образа воркера, в сети стека
docker compose --profile test run --rm test
```

Сквозной: настоящие API, очередь, S3 и OCR; экстрактор — заготовка `tests/e2e/fake_responses.json`, ключ не нужен.
Fake-режим включается парой `DOCFLOW_FAKE_EXTRACTOR=1` + `DOCFLOW_ALLOW_FAKE=1` (обе в `docker-compose.e2e.yml`):
с одним первым флагом воркер не стартует.

```
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
uv run pytest -m e2e -v
```

Без контейнеров: `uv run uvicorn --factory docflow.api.app:create_app` и `uv run arq docflow.worker.WorkerSettings`
(настройки — `.env` по образцу `.env.example`; для OCR нужны `tesseract-ocr` с `rus` и `ghostscript`).

## Качество

Точность извлечения меряется на эталонных наборах (формат — `evals/README.md`; настоящие документы в git не
попадают):

```
ANTHROPIC_API_KEY=sk-... uv run docflow eval evals/invoice_ru --extractor claude
```

Отчёт печатается и пишется в `evals/eval-report.json`. Опубликованных замеров на реальных документах пока нет.

## Запуск в проде

API: `uvicorn --factory docflow.api.app:create_app`. Размер тела `POST /v1/documents` ограничен в самом приложении
(`DOCFLOW_MAX_REQUEST_MB`, по умолчанию 200), но лимит нужен и на прокси перед ним — иначе прокси сам буферизует
гигабайты: в nginx `client_max_body_size 200m;` (не больше `DOCFLOW_MAX_REQUEST_MB`).
Задайте `DOCFLOW_PUBLIC_BASE_URL` (попадает в `pdf_url`).

Смените все три секрета: `DOCFLOW_PG_PASSWORD`, `DOCFLOW_REDIS_PASSWORD`, `DOCFLOW_S3_ACCESS_KEY` +
`DOCFLOW_S3_SECRET_KEY` (пароль Postgres применяется только при создании тома `pgdata`; на живой базе — `ALTER
USER`). Пароли подставляются в URL без экранирования — используйте буквы, цифры, `-` и `_`.

Никогда не публикуйте наружу порты Redis, Postgres и S3 — в compose они привязаны к `127.0.0.1`, так и оставьте.
Особенно Redis: arq хранит в нём задачи в pickle, и любой, кто может записать в Redis, исполнит свой код в
воркере. API и сайт в `docker-compose.yml` тоже опубликованы только на `127.0.0.1`; в проде ставьте их за прокси с
TLS и публикуйте только прокси.
