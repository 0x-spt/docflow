# Коннекторы — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Выгрузка документов в Google Таблицы, Битрикс24, amoCRM, 1С (OData), уведомления в Telegram/почту,
реестр Excel, правила маршрутизации и согласования, управление всем этим из кабинета.

**Architecture:** Получатели (Destination) с зашифрованными секретами и маппингом полей; правила маршрутизации
решают, куда и что; записи Export — единица доставки с ретраями через arq (как вебхуки), идемпотентные и во
внешней системе. Коннекторы — классы с `test()` и `export()` поверх httpx.

**Tech Stack:** FastAPI, SQLAlchemy, arq, httpx, cryptography (Fernet), pyjwt (Google JWT), openpyxl,
aiosmtplib или smtplib в потоке; Next.js кабинет.

**Spec:** `docs/superpowers/specs/2026-10-28-connectors-design.md`

## Global Constraints

- Ветка `connectors` от `main` (7f11e72). Коммиты без Co-Authored-By. `UV_CACHE_DIR=E:/dev/uv-cache`, npm на E:.
  Не трогать PostgreSQL на :5432. Тексты — по-русски, ruff 120, стиль существующего кода.
- Все внешние HTTP-вызовы — httpx `AsyncClient` с таймаутом 20 с; адреса из настроек клиента проходят
  `check_webhook_url` (SSRF) — кроме случаев `webhook_allow_private=true` (dev/e2e).
- Секреты: Fernet, ключ `DOCFLOW_SECRETS_KEY` (base64 32 байта); без ключа API отказывается сохранять секреты
  (422 «на сервере не настроен ключ шифрования») и воркер помечает выгрузки `failed` с этой причиной.
  Секреты никогда не возвращаются API и не пишутся в лог.
- Ошибки коннекторов — `ConnectorError(message: str, retryable: bool)`; сообщение по-русски, без секретов.
- Тесты внешних API — respx по документированным контрактам; живые аккаунты не используются.

## Review Focus

1. Повторная доставка (ретрай после таймаута, повторное одобрение, `retry`) не создаёт дубль во внешней системе.
2. `needs_review`/`failed` не уходят в учётные системы (1С, CRM, таблицы); уведомление «на проверку» — уходит.
3. Получатель удалён или отключён между постановкой и доставкой → выгрузка `failed` «получатель отключён», без падения.
4. Документ без ИНН/номера/таблицы → коннектор делает разумное (компания по названию, без позиций), не падает.
5. Ответ 429/5xx внешней системы → ретрай; 4xx с неверными данными → `failed` без бесконечных ретраев.

---

### Task 1: Ядро: модели, шифрование, API получателей и правил

**Files:** Create `src/docflow/secrets.py`, `src/docflow/connectors/__init__.py` (реестр), `src/docflow/connectors/base.py`,
`src/docflow/api/destinations.py`, `src/docflow/api/routing.py`, `migrations/versions/0007_connectors.py`;
Modify `db/models.py`, `config.py`, `api/app.py`, `.env.example`, `docker-compose.yml` (DOCFLOW_SECRETS_KEY без
дефолта в проде; для dev — значение в `.env.example` с пометкой).

**Interfaces (Produces):**
- `encrypt_secrets(d: dict, key: str) -> str`, `decrypt_secrets(token: str, key: str) -> dict`, `SecretsKeyMissing`.
- Модели: `Destination(id, tenant_id, kind, name, config: JSON, secrets_enc: str|None, mapping: JSON, active,
  created_at, updated_at)`; `RoutingRules(tenant_id PK, rules: JSON, updated_at)` (весь список одним документом);
  `Export(id, tenant_id, document_id, destination_id, purpose: 'export'|'notify_review'|'notify_new', status:
  'pending'|'done'|'failed', attempt, next_attempt_at, last_error, external_id, created_at, updated_at)`,
  уникальность (document_id, destination_id, purpose).
- `base.py`: `ConnectorError`, `ExportDoc` (id, tenant_id, schema: DocSchema, data, status, created_at, title,
  number, issued, cabinet_url, digital_pdf_url, scan_pdf_url, async `load_digital_pdf()`, `load_scan_pdf()`),
  `DestCtx(config, secrets, mapping, http)`, протокол `Connector(kind, label, config_fields, secret_fields,
  target_fields, test(ctx), export(ctx, doc) -> external_id, notify(ctx, doc, purpose) -> None)`;
  `REGISTRY: dict[str, Connector]`, `get_connector(kind)`.
- Схема правила (pydantic): `Rule(name, schema: str|None, conditions: list[Condition], export_to: list[UUID],
  require_approval: bool, notify: list[UUID])`, `Condition(field, op: 'eq'|'ne'|'gt'|'ge'|'lt'|'le'|'contains'|
  'in_dictionary'|'empty'|'not_empty', value: str|float|None)`.
- API: `GET/POST /v1/destinations`, `GET/PATCH/DELETE /v1/destinations/{id}`, `POST /v1/destinations/{id}/test`;
  `GET/PUT /v1/routing-rules`; `GET /v1/connectors` (описание типов: поля config/секретов/целевые поля для UI).
  Права: владелец или ключ API (как вебхуки); оператор — только GET. Ответы без секретов (`has_secrets`).
- В тестах — фиктивный коннектор `test_fake` регистрируется в conftest.

- [ ] Тесты (RED): шифрование туда-обратно и без ключа; CRUD получателей (секреты не возвращаются, PATCH без
  секретов их сохраняет, изоляция тенантов, оператор 403 на запись); правила: валидация ссылок на чужих/нет
  получателей → 422, неизвестный оператор → 422; `/test` вызывает коннектор и отдаёт `{ok, message}`; миграция.
- [ ] Реализация; зелёные pytest/ruff/tests/db. Commit `feat: получатели выгрузки, правила маршрутизации, шифрование секретов`.

### Task 2: Движок: правила → выгрузки, очередь, ретраи, согласование, триггеры

**Files:** Create `src/docflow/routing.py`, `src/docflow/exports.py`; Modify `pipeline.py`, `review.py`,
`api/documents.py`, `worker.py`, `queue.py`, `serializers.py`; Create `src/docflow/api/exports.py`.

**Interfaces:**
- `evaluate_rules(rules, schema, data, dictionaries) -> RoutingDecision(export_to: set[UUID], notify: set[UUID],
  approval: list[str])` (условия по нормализованным данным; числа сравниваются как числа, даты как даты).
- Конвейер: после статуса `ok` — если есть правило с согласованием → статус `needs_review` + замечание
  `approval_required`; иначе ставит выгрузки `export`. При `needs_review` — уведомления `notify_review`.
  Одобрение оператором → выгрузки `export` и `notify_new`.
- `enqueue_exports(session, document, purpose, destination_ids, enqueuer)`; задача arq `deliver_export`
  (ретраи 10 с/1 мин/5 мин/30 мин/2 ч, 6 попыток, ConnectorError.retryable решает; 429/5xx/таймаут — retryable);
  `requeue_stale` подбирает зависшие `pending`.
- API: `GET /v1/exports`, `POST /v1/exports/{id}/retry` (только `failed`); `document_payload.exports`.

- [ ] Тесты: правила (все операторы, схема, несколько правил объединяются); согласование переводит в
  needs_review и после одобрения выгружает; needs_review не выгружается, но уведомляет; дубль не создаётся
  (повтор enqueue, повтор одобрения); ретраи и финальный failed; отключённый получатель → failed; retry.
- [ ] Commit `feat: маршрутизация документов и доставка выгрузок с повторами`.

### Task 3: Excel и Google Таблицы
**Files:** `connectors/google_sheets.py`, `src/docflow/export_xlsx.py`, `api/exports.py` (Excel endpoint).
- Excel: `GET /v1/exports/excel?schema=&from=&to=&status=ok` → xlsx (листы «Документы», «Позиции»; заголовки
  по `title`; числа числами, даты датами). Лимит 5000 документов (422 «сузьте период»).
- Google: JWT сервисного аккаунта (RS256, scope spreadsheets) → токен; `test` = GET метаданных таблицы;
  `export`: создать листы/заголовки при отсутствии, поиск id документа в колонке A (идемпотентность),
  `values:append` строки документа и позиций. Маппинг по умолчанию — все поля схемы по порядку.
- [ ] Тесты (respx): токен, test, первая выгрузка с заголовками, повтор не дублирует, 403 «нет доступа к
  таблице — откройте доступ для <email>» не retryable, 429 retryable; xlsx читается openpyxl и содержит данные.
- [ ] Commit `feat: реестр Excel и выгрузка в Google Таблицы`.

### Task 4: Битрикс24 и amoCRM
**Files:** `connectors/bitrix24.py`, `connectors/amocrm.py`.
- Битрикс24 (входящий вебхук): `test` = `profile`; компания по ИНН (`crm.requisite.list` по RQ_INN → ENTITY_ID)
  или по названию (`crm.company.list` filter TITLE), иначе `crm.company.add` (+ реквизит с ИНН/КПП);
  идемпотентность: `crm.deal.list` filter по полю-метке (`ORIGIN_ID = docflow:<id>`, `ORIGINATOR_ID=docflow`);
  `crm.deal.add` (TITLE, OPPORTUNITY, CURRENCY_ID RUB, COMPANY_ID, ASSIGNED_BY_ID из config/правила, поля
  mapping), `crm.timeline.comment.add` со ссылками на PDF.
- amoCRM (долгосрочный токен): `test` = `GET /api/v4/account`; идемпотентность — поиск сделки по `query=docflow:<id>`
  (метка в названии примечания) ; `POST /api/v4/leads/complex` (name, price, _embedded.companies с ИНН в
  custom_fields по mapping, tags DocFlow); примечание `POST /api/v4/leads/{id}/notes` со ссылками.
- [ ] Тесты (respx) на оба: test, создание компании и сделки, найденная компания по ИНН, повтор без дубля,
  401 не retryable с понятным текстом, 503 retryable, документ без ИНН.
- [ ] Commit `feat: выгрузка в Битрикс24 и amoCRM`.

### Task 5: 1С через OData
**Files:** `connectors/onec_odata.py`.
- Basic auth; `test` = `$metadata` (проверка наличия объектов из config); контрагент: `Catalog_Контрагенты?$filter=ИНН eq '…'`
  → создать при отсутствии (Description, ИНН, КПП); идемпотентность: поиск документа по
  `Комментарий` содержит `docflow:<id>`; создание `Document_ПоступлениеТоваровУслуг` (Date, Контрагент_Key,
  Комментарий, НомерВходящегоДокумента, ДатаВходящегоДокумента, Товары: Номенклатура_Key по поиску
  `Catalog_Номенклатура?$filter=Description eq '…'`, Количество, Цена, Сумма) — номенклатура не найдена →
  ConnectorError не retryable «нет номенклатуры: …»; вид документа и имена объектов/полей — в `config` с
  умолчаниями БП 3.0; проводить не нужно (решение: документ создаётся непроведённым, бухгалтер проверяет).
- [ ] Тесты (respx) по формату OData 1С: test, новый и найденный контрагент, документ с позициями, повтор без дубля,
  нет номенклатуры, 401.
- [ ] Commit `feat: выгрузка в 1С через OData`.

### Task 6: Уведомления Telegram и почта
**Files:** `connectors/telegram.py`, `connectors/email.py`.
- Telegram: `test` = `getMe`; `notify(purpose)`: текст «Нужна проверка: <тип> № <номер> — <причины>» /
  «Новый документ: …» + ссылка в кабинет (`public_site_url` из настроек, новый `DOCFLOW_PUBLIC_SITE_URL`).
- Почта: SMTP (STARTTLS/SSL по config) через smtplib в потоке; `test` = login; то же содержание, тема.
- [ ] Тесты: respx для Telegram; почта — подменённый SMTP (фикстура с aiosmtpd или monkeypatch smtplib).
- [ ] Commit `feat: уведомления в Telegram и на почту`.

### Task 7: Кабинет: подключения и маппинг
**Files:** `web/src/app/app/integrations/*` (новый раздел «Подключения», ссылка в навигации), формы по типу из
`GET /v1/connectors`, «Проверить подключение», включить/отключить/удалить (двухшаговое), маппинг полей
(схема → целевые поля коннектора). Секретные поля — `type=password`, при редактировании пустые = не менять.
- [ ] lint/tsc/build; Commit `feat(web): подключения к системам в кабинете`.

### Task 8: Кабинет: правила, выгрузки документа, реестр Excel
**Files:** `web/src/app/app/rules/*`, документ — блок «Выгрузки» с «Повторить», очередь — «Реестр в Excel»
(период, схема) через route handler, обновить e2e: правила сохраняются, Excel скачивается, блок выгрузок виден.
- [ ] lint/tsc/build/e2e; Commit `feat(web): правила маршрутизации, выгрузки и реестр Excel`.

### Task 9: Документация «где взять ключи»
**Files:** `README.md` (раздел «Подключения»: пошагово для Google сервисного аккаунта, Битрикс24 входящего вебхука,
amoCRM долгосрочного токена, публикации 1С OData, Telegram-бота, SMTP; `DOCFLOW_SECRETS_KEY` и как его
сгенерировать), подсказки в формах кабинета ссылаются на эти шаги.
- [ ] Commit `docs: как подключить 1С, CRM, таблицы и уведомления`.
