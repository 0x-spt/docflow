# Свои названия полей и сопоставление с системами клиента: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** компания сама называет поля документов в одном месте кабинета. Названия видны в очереди, документе, PDF,
таблицах, Excel и уведомлениях. Google Таблицы клиента принимают строку по названиям колонок. Поля CRM
подтягиваются из самой системы и подбираются автоматически.

**Architecture:**
- Подписи — отдельная таблица `field_labels` поверх схемы. Модуль `docflow/labels.py`: чистая `apply_labels` и
  загрузчики. Подписи применяются только там, где поля видят люди; конвейер извлечения и `/json-schema` их не
  видят.
- Нормализация названий и автоподбор — чистый модуль `docflow/fieldmatch.py`.
- Коннекторы получают необязательный метод `remote_fields`. Эндпоинт `POST /v1/destinations/{id}/remote-fields`
  отдаёт поля системы и предложения.
- Кабинет: новая страница `/app/fields`, кнопка «Подтянуть поля» в форме подключения. `fieldLabel` в вебе
  начинает брать `title`, поэтому подписи из `GET /v1/schemas/{slug}` сами попадают в документ, форму
  проверки, правила и сопоставление.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, pytest (SQLite в `tests/api`, respx для
коннекторов), Next.js 15 / React 19.

**Spec:** `docs/superpowers/specs/2026-10-29-field-names-design.md`

## Global Constraints

- Коммиты без `Co-Authored-By` и без «Generated with Claude Code».
- Реальные документы и персональные данные не коммитить. В тестах только синтетика: ИНН вида `7707083893` уже
  есть в тестах, новые реквизиты не выдумывать.
- Секреты (вебхук Битрикс24, токен amoCRM, ключ сервисного аккаунта) не попадают в ответы API, логи и тексты
  ошибок. Наружу из неожиданного исключения — только `type(exc).__name__`, как в `/test`.
- Стиль и плотность комментариев как в окружающем коде, комментарии и сообщения по-русски. `ruff check src tests`
  чист (line-length 120).
- Существующие тесты остаются зелёными. Меняются только тесты, чьё поведение спека меняет явно; каждый такой тест
  назван в задаче.
- Команды: `.venv/Scripts/python -m pytest -q`, `.venv/Scripts/ruff check src tests`. Веб: в `web/`
  `npm run lint` и `npm run build`, кэш npm остаётся на E:.
- Postgres только compose на `127.0.0.1:55432` (`pytest -m integration tests/db`). Нативный Postgres на
  `127.0.0.1:5432` не трогать.
- Подписи: после `strip()` не пустые, ≤ 80 символов, без управляющих символов. Ключ — поле верхнего уровня или
  `таблица.колонка` последней версии схемы. Не больше 3 полей очереди. Ошибки 422 по-русски.
- `Document.data` всегда хранится по служебным именам. Подписи не меняют `data`, промпт и `/json-schema`.
- Служебные колонки таблиц ровно такие: `ID документа`, `Тип`, `Номер`, `Дата`, `Статус`,
  `Цифровая версия (PDF)`, `Кабинет`.

## Review Focus

1. **Схема получила новую версию, где поля с подписью больше нет** (или старый документ на версии без нового
   поля). Ожидание: устаревший ключ молча игнорируется в показе, `GET …/labels` его не показывает, ничего не
   падает. Тест: задача 1 `test_stale_label_keys_ignored`.
2. **Шапка клиента с пустыми ячейками, дублями и хвостом пустых колонок.** Ожидание: пустые заголовки не
   совпадают ни с чем, обе одинаковые колонки получают значение, строка по длине равна шапке. Тест: задача 4
   `test_align_blank_and_duplicate_headers`.
3. **Подпись совпадает со служебной колонкой** («Статус», «Кабинет»). Ожидание: 422 при сохранении. Иначе в
   таблице клиента колонка «Статус» получила бы статус проверки вместо значения поля. Тест: задача 1
   `test_label_cannot_take_service_column`.
4. **Схема документа в очереди не находится** (своя схема тенанта удалена, документ ссылается на версию, которой
   нет). Ожидание: список не падает, сводка — по служебным именам. Тест: задача 3
   `test_list_survives_unknown_schema`.
5. **amoCRM всегда отдаёт ссылку `next`** (огромный аккаунт или кривой ответ). Ожидание: не больше 20 страниц,
   без бесконечного цикла. Тест: задача 5 `test_amocrm_remote_fields_page_cap`.

## Решения исполнителя (спека их не фиксирует)

- Уникальность подписей проверяется и среди колонок одной таблицы: они становятся колонками листа «Позиции».
- Подпись не может совпадать со служебной колонкой, кроме «Номер» и «Дата»: это исходные названия `number` и
  `issued`.
- 422 по подписям приходит списком `detail` в формате FastAPI (`loc`/`msg`). Так кабинет показывает ошибку у
  поля, а прежний разбор `detail` продолжает работать.
- Правило «первое строковое поле без правил» исключает любые правила поля, включая `regex`. Иначе у паспорта
  контрагентом стала бы серия.
- Уведомления меняются только там, где у поля есть подпись компании. Без переименований текст писем прежний.
- В комментариях CRM полей нет (только ссылки и метка), поэтому подписям там применяться не к чему.
- У шапки клиента без колонки «ID документа» проверки дублей нет. Шапку клиента мы не меняем, а повтор выгрузки
  бывает только после сбоя.
- Шапка «своя» считается переименованной, если совпали число колонок и служебные колонки на местах.
  Другую схему с тем же числом колонок по одним заголовкам не отличить; по спеке так и задумано.
- Свой подбор 1С — необязательный метод коннектора `suggest_mapping`, а не проверка `kind` в эндпоинте.
- УПД правится на месте в версии 1 (обоснование в задаче 3).

---

### Task 1: Хранение подписей и API

**Files:**
- Create: `src/docflow/labels.py`
- Create: `src/docflow/fieldmatch.py` (пока только `normalize`; задача 5 добавит автоподбор)
- Create: `migrations/versions/0010_field_labels.py`
- Modify: `src/docflow/db/models.py` (модель `FieldLabels` после `RoutingRules`)
- Modify: `src/docflow/api/schemas.py` (`GET /{slug}` с подписями, `GET/PUT /{slug}/labels`)
- Test: `tests/test_labels.py`, `tests/api/test_field_labels_api.py`

**Interfaces:**
- Produces:
  - `fieldmatch.normalize(text: str) -> str`: `casefold`, ё→е, любые пробельные символы в один пробел, обрезка
    по краям пробелов и знаков `.,;:!?()[]{}«»"'*-–—/\`.
  - `FieldLabels`, таблица `field_labels`: `id` (uuid PK), `tenant_id` (FK tenants, CASCADE), `schema_slug: str`,
    `labels: JsonType` (dict, default `{}`), `queue_fields: JsonType` (list, default `[]`), `updated_at`.
    `UniqueConstraint("tenant_id", "schema_slug", name="tenant_slug")`. Миграция `0010`, `down_revision="0009"`.
  - `LABEL_MAX = 80`, `QUEUE_MAX = 3`, `SERVICE_HEADERS: tuple[str, ...]` — семь служебных колонок из Global
    Constraints. Константа живёт здесь; `tabular` импортирует её в задаче 4.
  - `@dataclass(frozen=True) LabelSet(labels: dict[str, str], queue_fields: list[str])`, `EMPTY_LABELS`.
  - `apply_labels(schema: DocSchema, labels: Mapping[str, str] | None) -> DocSchema` — глубокая копия с
    заменённым `title` у полей и колонок. Неизвестные ключи и пустые значения игнорируются, исходник не меняется.
  - `label_keys(schema) -> list[str]` — поля верхнего уровня (и таблицы), затем `таблица.колонка`.
  - `class LabelsInvalid(ValueError)` с `errors: list[tuple[list[str], str]]` — пары (loc, сообщение). Loc:
    `["labels", key]` или `["queue_fields"]`.
  - `check_label_set(schema, labels: dict[str, str], queue_fields: list[str]) -> LabelSet` — чистит (strip,
    убирает пустые) и проверяет. Собирает все ошибки и бросает одну `LabelsInvalid`.
  - `total_field(schema) -> str | None`, `counterparty_field(schema) -> str | None`,
    `default_queue_fields(schema) -> list[str]`, `queue_fields_for(schema, stored: list[str]) -> list[str]`.
  - `async get_label_set(session, tenant_id, slug) -> LabelSet`.
  - `async get_label_sets(session, tenant_id, slugs: Iterable[str]) -> dict[str, LabelSet]` — один запрос.
  - `async resolve_labeled(resolver, session, tenant_id, slug, version) -> DocSchema` — resolve +
    `apply_labels`. `LookupError` пробрасывает.
  - API:
    - `GET /v1/schemas/{slug}/labels` →
      `{slug, title, version, rows: [{key, type, group, default, label, queue}], queue_fields, queue_default}`.
      `group` — имя таблицы у колонок, иначе `null`. `default` — `title or name` из схемы без подписей. `label` —
      подпись компании или `""`. `queue` — входит ли поле в действующий набор очереди.
    - `PUT /v1/schemas/{slug}/labels`, тело `{labels: {...}, queue_fields: [...]}`, заменяет набор целиком и
      отвечает как GET.

Правила проверки в `check_label_set` (сообщения дословно):
- ключ не из `label_keys(последняя версия)` → «нет такого поля в схеме»;
- после strip длиннее 80 → «не длиннее 80 символов»;
- есть символ с кодом < 32 или 127 → «без управляющих символов»;
- среди нетабличных полей верхнего уровня действующие названия (подпись или `default`) повторяются без учёта
  регистра → «такое название уже у поля «<действующее название другого>»». Сравниваются только пары, где хотя бы
  одна сторона — подпись компании, чтобы старые дубли в своих схемах не ломали сохранение. Так же проверяются
  колонки одной таблицы между собой;
- `normalize(label)` совпадает с одной из `SERVICE_HEADERS` кроме «Номер» и «Дата» → «так называется служебная
  колонка таблиц»;
- `queue_fields`: больше 3 → «в очереди не больше 3 полей», ключ не нетабличное поле верхнего уровня → «в
  очередь можно только поле верхнего уровня: «<ключ>»», дубли убираются молча.

Умолчание очереди `default_queue_fields(schema)` — по порядку, без дублей:
1. `counterparty_field` — первое поле `type == "string"` без правил поля, не `number` и не
   `account`-параметр правила `account_matches_bik`. Для счёта и УПД это `supplier`, для паспорта `full_name`.
2. `total_field` — `params["total"]` правила `total_consistent`, иначе первого правила с параметром `total`,
   иначе поле `total` типа number/integer.
3. Первая дата `type == "date"` кроме `issued`.
4. Никогда не берутся поля с правилами `inn`/`kpp`/`bik`/`ogrn`/`snils` и счёт из `account_matches_bik`.

`queue_fields_for(schema, stored)` оставляет из `stored` ключи, которые есть в этой версии схемы. Если не осталось
ничего, отдаёт умолчание.

- [ ] **Step 1: юнит-тесты `tests/test_labels.py`** (на `builtin_schemas()`):
  - `test_apply_labels_replaces_titles_and_keeps_source`: `apply_labels(inv, {"vat": "Налог", "items.qty": "Кол-во"})`
    → у `vat` title «Налог», у колонки `qty` «Кол-во». Исходный `inv` не изменён. `to_json_schema(inv)` до и после
    вызова равны.
  - `test_stale_label_keys_ignored`: `apply_labels(inv, {"gone": "X", "vat": "  "})` равен `inv` по
    `model_dump()`.
  - `test_check_rejects_bad_values`: 81 символ, `"a\nb"`, `"нет_поля"` → `LabelsInvalid`. В `errors` три записи с
    loc `["labels", key]` и сообщениями выше.
  - `test_check_duplicate_case_insensitive`: `{"vat": "всего к оплате"}` (у `total` в invoice title «Всего к
    оплате») → ошибка у `vat`. `{"vat": "Налог", "total": "налог"}` → ошибка.
  - `test_normalize`: `"  Всё — ИТОГО: "` → `"все — итого"` (тире внутри остаётся), `"«ИНН»"` → `"инн"`,
    `"Сумма  к  оплате"` → `"сумма к оплате"`.
  - `test_label_cannot_take_service_column`: `{"vat": "Статус"}` → ошибка. `{"number": "Номер"}` → ок.
  - `test_queue_fields_rules`: 4 поля → ошибка `["queue_fields"]`, `["items"]` → ошибка,
    `["total", "total"]` → `["total"]`.
  - `test_default_queue_fields`: invoice → `["supplier", "total"]`, upd → `["supplier", "total"]`, passport →
    `["full_name", "birth_date"]`.
  - `test_queue_fields_for_falls_back`: `queue_fields_for(inv, ["gone"]) == default_queue_fields(inv)`.
- [ ] **Step 2: API-тесты `tests/api/test_field_labels_api.py`** (фикстуры `api`, `auth`, `make_tenant`,
  `session_for` из `tests/api`):
  - `test_get_labels_rows`: GET `/v1/schemas/invoice_ru/labels` → 200. Есть строки `vat` (`group: null`,
    `default` = title из YAML, `label: ""`) и `items.qty` (`group: "items"`), `queue_default: true`,
    `queue_fields == ["supplier", "total"]`.
  - `test_put_then_schema_shows_labels`: PUT `{labels: {"vat": "Налог"}, queue_fields: ["total"]}` → 200,
    `queue_default: false`. Затем `GET /v1/schemas/invoice_ru` и `GET /v1/schemas/invoice_ru?version=1` → у `vat`
    title «Налог». `GET /v1/schemas/invoice_ru/json-schema` равен ответу до PUT.
  - `test_put_replaces_whole_set`: второй PUT `{labels: {}, queue_fields: []}` → подписи сброшены,
    `queue_default: true`.
  - `test_put_422_field_errors`: `{labels: {"vat": "x" * 81}}` → 422, `detail` — список
    `[{"loc": ["body", "labels", "vat"], "msg": "не длиннее 80 символов", "type": "value_error"}]`.
  - `test_labels_isolated_between_tenants`: подписи тенанта A не видны в GET схемы и labels у тенанта B.
  - `test_operator_cannot_put_labels`: сессия оператора → PUT 403, GET 200.
  - `test_labels_unknown_schema_404`: GET и PUT `/v1/schemas/nope/labels` → 404.
- [ ] **Step 3:** прогнать оба файла → FAIL (модуля нет).
- [ ] **Step 4: реализация.**
  - Модель и миграция: имена ограничений через `op.f(...)` как в `0007`. `JSON_TYPE` с вариантом JSONB.
  - `api/schemas.py`:
    - `get_schema` использует `resolve_labeled`;
    - `/json-schema` не меняется;
    - роуты `/{slug}/labels` объявить до `/{slug}`;
    - PUT через `require_owner_or_key` (как `routing.py`): upsert строки, при `IntegrityError` rollback и
      одно повторное чтение и обновление;
    - `LabelsInvalid` → `HTTPException(422, detail=[{"loc": ["body", *loc], "msg": msg, "type": "value_error"}])`;
    - проверка по последней версии `resolver.resolve(..., None)`, `LookupError` → 404.
- [ ] **Step 5:** оба файла и `tests/db/test_migrations.py` (SQLite) → PASS; `pytest -q` весь, ruff.
- [ ] **Step 6:** коммит `feat(labels): свои названия полей компании — хранение и API`.

### Task 2: Подписи в PDF, выгрузках, Excel и уведомлениях

**Files:**
- Modify: `src/docflow/connectors/base.py` (`ExportDoc.base_schema`)
- Modify: `src/docflow/exports.py` (`build_export_doc`, `_attempt`)
- Modify: `src/docflow/api/exports.py` (Excel), `src/docflow/api/documents.py` (`digital.pdf`)
- Modify: `src/docflow/connectors/notify_common.py`
- Test: `tests/test_exports.py`, `tests/api/test_exports_excel_api.py`, `tests/api/test_documents.py`,
  `tests/test_notify_connectors.py` (новые тесты в существующих файлах)

**Interfaces:**
- Consumes: `apply_labels`, `get_label_set`, `LabelSet`, `total_field` из задачи 1.
- Produces:
  - `ExportDoc.base_schema: DocSchema | None = None` — схема без подписей. Свойство
    `ExportDoc.default_schema -> DocSchema` отдаёт `base_schema or schema`. Тесты, где `ExportDoc` собирается
    руками, не меняются.
  - `build_export_doc(document, schema, settings, blobs, labels: Mapping[str, str] | None = None) -> ExportDoc`:
    `schema` — исходная, `ExportDoc.schema = apply_labels(schema, labels)`, `base_schema = schema`. Цифровой PDF
    внутри рендерится по схеме с подписями.
  - `notify_common.field_title(doc, key) -> str | None` — подпись компании для поля верхнего уровня: title из
    `doc.schema`, если он отличается от title в `doc.default_schema`, иначе `None`.

- [ ] **Step 1: тесты.**
  - `test_build_export_doc_applies_labels` (`tests/test_exports.py`):
    `build_export_doc(..., labels={"vat": "Налог"})` → `doc.schema` с подписью, `doc.base_schema` без неё,
    `doc.data` с ключом `vat`.
  - `test_deliver_uses_tenant_labels` (рядом с тестами `deliver_export` на `fake_connector`): строка
    `FieldLabels` тенанта → в `fake_connector.docs[0].schema` у `vat` «Налог». У другого тенанта без подписей —
    исходный title.
  - `test_excel_headers_use_labels` (`tests/api/test_exports_excel_api.py`): PUT подписи `vat` → в xlsx на листе
    «Документы» заголовок «Налог» вместо исходного.
  - `test_digital_pdf_uses_labels` (`tests/api/test_documents.py`, фикстура `digital_font`): monkeypatch
    `render_digital_pdf` ловит схему → у `vat` title «Налог».
  - `test_notify_new_uses_total_label` (`tests/test_notify_connectors.py`): у `doc.schema` total с подписью «К
    оплате», `base_schema` исходная → вторая строка `"ООО Ромашка, К оплате: 12 500,50 ₽"`.
  - `test_notify_review_reason_prefixed_by_label`: issue `{"field": "vat", ...}` и подпись `vat` «Налог» →
    `"Причины: Налог: <сообщение>"`. Без подписи текст прежний. Существующие `test_telegram_notify_new_text` и
    `test_telegram_notify_review_reasons` не меняются.
- [ ] **Step 2:** прогнать → FAIL.
- [ ] **Step 3: реализация.**
  - `_attempt` после resolve берёт `get_label_set(session, document.tenant_id, document.schema_slug)` и передаёт
    `labels` в `build_export_doc`.
  - Excel: `labels` один раз на запрос, `build_registry(apply_labels(latest, labels), docs)`.
  - `digital.pdf`: `apply_labels` перед рендером.
  - `notification_text`:
    - `notify_new`: при подписи у `total_field(doc.default_schema)` пишется `«{подпись}: {money}»`, иначе
      `«сумма {money}»`;
    - `reasons`: сообщение с полем верхнего уровня, у которого есть подпись, — `«{подпись}: {сообщение}»`.
  - Комментарии CRM полей не перечисляют (только ссылки и метка), там менять нечего.
- [ ] **Step 4:** тесты → PASS, весь `pytest -q`, ruff.
- [ ] **Step 5:** коммит `feat(labels): названия компании в PDF, Excel, выгрузках и уведомлениях`.

### Task 3: Очередь — сводка по выбору компании, замечание только к проверке, правка УПД

**Files:**
- Modify: `src/docflow/api/documents.py` (`_list_item`, `list_documents`)
- Modify: `src/docflow/schemas/builtin/upd_ru.yaml`
- Test: `tests/api/test_documents.py`, `tests/schemas/test_library.py`

**Interfaces:**
- Consumes: `get_label_sets`, `queue_fields_for`, `apply_labels`, `EMPTY_LABELS` из задачи 1, `ResolverDep`.
- Produces: элемент `summary` в `GET /v1/documents` — `{"field": str, "label": str, "value": str | int | float}`.

- [ ] **Step 1: тесты.**
  - Обновить `test_list_item_shape` (поведение меняет спека). Те же данные, статус `needs_review` → `summary ==
    [{"field": "supplier", "label": "Поставщик", "value": "ООО"}, {"field": "total", "label": "Всего к оплате",
    "value": 250}]`. Ключ `a` вне схемы больше не показывается. `issue` прежний.
  - `test_list_summary_uses_company_choice_and_labels`: PUT labels `{labels: {"total": "К оплате"}, queue_fields:
    ["total", "vat"]}` → в `summary` ровно `total` с label «К оплате» и `vat`. Пустые значения пропускаются, как
    раньше.
  - `test_list_issue_only_for_review_and_failed`: документ `ok` с issue `approval_required` (не из `INFO_CODES`)
    → `issue is None`. У `needs_review` и `failed` — сообщение.
  - `test_list_survives_unknown_schema`: документ с `schema_slug="gone"` → 200, `summary` по полям `data` в
    прежнем порядке «первые три», `label == field`.
  - `test_upd_titles_fixed` (`tests/schemas/test_library.py`): у `upd_ru` поле `vat` с title «НДС», у `account` в
    описании «Расчётный счёт» и нет «Корреспондентский».
- [ ] **Step 2:** прогнать → FAIL.
- [ ] **Step 3: реализация.**
  - `list_documents`:
    - различные `(slug, version)` страницы разрешаются по одному разу через resolver; `LookupError` → `None`;
    - `get_label_sets` одним запросом.
  - `_list_item(document, schema: DocSchema | None, label_set: LabelSet)`:
    - схема есть: ключи `queue_fields_for(schema, label_set.queue_fields)`, label —
      `apply_labels(schema, labels)`, title поля или ключ;
    - схемы нет: прежний обход `data`;
    - `issue` только при `status in ("needs_review", "failed")`.
  - УПД:
    - `vat.title: НДС`, `vat.description: "Сумма НДС (графа 8) итоговой строки «Всего к оплате». null, если «без
      НДС»."`;
    - `account.description: "Расчётный счёт продавца (р/с), 20 цифр без пробелов. null, если реквизитов нет."`;
    - версия остаётся 1 (решение ниже).
- [ ] **Step 4:** тесты → PASS, весь `pytest -q` (тесты `seed-schemas` в `tests/test_cli.py` тоже), ruff.
- [ ] **Step 5:** коммит `feat(queue): сводка по выбору компании, замечания только к проверке; fix(upd): подписи НДС и р/с`.

Решение по УПД: правка на месте, версия 1. `_seed_schemas` обновляет `body` у той же `(slug, version)`, а
`builtin_schemas()` читает YAML напрямую, так что правка доходит и до БД, и до стенда без сидирования.
Документы ссылаются на `(upd_ru, 1)`. С версией 2 старые документы показывали бы старую подпись, а перекрытия
тенантов, которые уже заняли v2 (`_latest_visible_version`), начали бы путаться со встроенной v2. Структура полей
не меняется, только тексты.

### Task 4: Google Таблицы подстраиваются под шапку

**Files:**
- Modify: `src/docflow/tabular.py`
- Modify: `src/docflow/connectors/google_sheets.py`
- Test: `tests/test_tabular.py`, `tests/test_google_sheets.py`

**Interfaces:**
- Consumes: `ExportDoc.default_schema` (задача 2), `SERVICE_HEADERS` и `fieldmatch.normalize` (задача 1).
- Produces:
  - `tabular.same_layout(existing: list[str], ours: list[str], *, lead: int, tail: int) -> bool` — та же длина,
    первые `lead` и последние `tail` ячеек совпадают. Хвостовые пустые ячейки `existing` отбрасываются.
  - `tabular.align_doc_row(doc, header: list[str], mapping, *, target) -> tuple[list[Cell], list[int]] | None` —
    строка длиной `len(header)` и индексы колонок с датами. `None`, если не совпала ни одна колонка.
  - `tabular.align_item_rows(doc, header: list[str], *, target) -> tuple[list[list[Cell]], list[int]] | None`.
  - `tabular.id_column(header: list[str]) -> int | None` — индекс колонки «ID документа» после нормализации.
  - `google_sheets.NO_KNOWN_COLUMNS = "в таблице нет ни одной знакомой колонки"`.

Варианты названий, по которым колонка клиента получает значение (сравнение после `normalize`):
- служебные: `ID документа`, `Тип`, `Номер`, `Дата`, `Статус`, `Цифровая версия (PDF)`, `Кабинет` — значения
  как в `doc_row`. Служебные проверяются первыми;
- поле верхнего уровня (не таблица): название из `mapping` (если ключ есть и значение непустое), `title` из
  `doc.schema` (подпись компании), `title or name` из `doc.default_schema`, `name`;
- лист позиций: `ID документа`, `Номер`, затем колонки таблицы с теми же вариантами (кроме mapping).

Пустой заголовок не совпадает ни с чем. Несколько колонок с одним названием получают одно значение.

Поведение `export`:
- Шапки нет: как сейчас, пишется наша, строка `doc_row`.
- Шапка наша (A1 == «ID документа»): если строка 1 равна `docs_header` — как сейчас. Если
  `same_layout(lead=4, tail=3)` (у позиций `lead=2, tail=0`) — шапка переписывается PUT A1, строка добавляется.
  Иначе `OTHER_COLUMNS`, не retryable.
- Шапка клиента (A1 другое): строка 1 → `align_doc_row`. `None` → `ConnectorError(NO_KNOWN_COLUMNS)` не
  retryable, без записи. Идемпотентность — по колонке `id_column`: читается `<буква>:<буква>`
  (FORMATTED_VALUE). Колонки ID нет — проверка дублей пропускается: шапку клиента не меняем, повтор после
  потерянного ответа может дописать строку (решение ниже). Формат дат — по индексам из `align_*`.

- [ ] **Step 1: тесты `tests/test_tabular.py`.**
  - `test_align_by_label_default_name_and_mapping`: шапка `["Сумма к оплате", "vat", "Поставщик", "Дата"]`,
    mapping `{"total": "Сумма к оплате"}`, подпись не задана → значения total, vat, supplier, дата документа
    (серийное число). Даты в индексах `[3]`.
  - `test_align_uses_company_label`: `doc.schema` с подписью `vat` «Налог», шапка `["налог "]` → значение vat.
  - `test_align_blank_and_duplicate_headers`: шапка `["", "Поставщик", "поставщик", "Мусор", ""]` → длина 5,
    обе колонки поставщика заполнены, остальные `None`.
  - `test_align_none_when_nothing_matches`: `["А", "Б"]` → `None`.
  - `test_same_layout`: только переименованные поля → True. Другое число колонок или сдвинутая служебная → False.
- [ ] **Step 2: тесты `tests/test_google_sheets.py`.** Расширить `Sheet._read`, чтобы диапазон `X:X` отдавал
  колонку X, а `1:1` — первую строку.
  - `test_our_header_renamed_fields_rewritten`: первая выгрузка, затем у второго документа `vat` с подписью →
    `puts` == 2, шапка в ячейках новая, строк 3.
  - `test_client_header_row_aligned_by_names`: `docs_col=[["Поставщик", "ID документа", "Сумма"]]` → дописана
    строка ровно `["ООО Ромашка", "'11111111-1111-1111-1111-111111111111", ""]`. id с префиксом `'`, как его
    отдаёт `text()`; `None` уходит пустой строкой. `puts == []`.
  - `test_client_header_dedupe_by_id_column`: повтор той же выгрузки не дописывает (ID во 2-й колонке).
  - `test_client_header_without_known_columns_fails`: `[["Мои заголовки"]]` → `ConnectorError(NO_KNOWN_COLUMNS)`,
    `retryable is False`, `appends == []`.
  - Обновить `test_date_format_applied_when_header_already_exists` (спека меняет поведение шапки клиента):
    `docs_col=[["Мои заголовки", "Дата"]]`, `items_col=[["ID документа", "Наименование"]]` → `puts == []`,
    формат дат есть у колонки с индексом 1 листа документов. Прежняя шапка позиций `[["ID"]]` теперь дала бы
    `NO_KNOWN_COLUMNS`, поэтому она заменена.
  - `test_other_schema_header_rejected_not_retryable` и `test_same_schema_header_accepted_on_next_document` не
    меняются и остаются зелёными.
- [ ] **Step 3:** прогнать → FAIL.
- [ ] **Step 4: реализация.**
  - `_Session.ensure_header` заменяется на `read_header(sheet) -> list[str]` (строка `1:1`) и решение в `export`
    по трём случаям выше.
  - `doc_row`, `docs_header` и Excel не меняются.
  - Обновить докстринг модуля: идемпотентность по колонке «ID документа».
- [ ] **Step 5:** тесты → PASS, весь `pytest -q`, ruff.
- [ ] **Step 6:** коммит `feat(sheets): шапка клиента — по названиям колонок, переименование своей шапки`.

### Task 5: Поля системы и автоподбор (бэкенд)

**Files:**
- Modify: `src/docflow/fieldmatch.py` (автоподбор поверх `normalize` из задачи 1)
- Modify: `src/docflow/connectors/base.py` (`RemoteField`, описание протокола)
- Modify: `src/docflow/connectors/{google_sheets,bitrix24,amocrm,onec_odata}.py`
- Modify: `src/docflow/api/destinations.py`
- Modify: `tests/conftest.py` (`FakeConnector.remote_fields`)
- Test: `tests/test_fieldmatch.py`, `tests/test_google_sheets.py`, `tests/test_bitrix24.py`, `tests/test_amocrm.py`,
  `tests/test_onec_odata.py`, `tests/api/test_destinations_api.py`

**Interfaces:**
- Consumes: `apply_labels`, `get_label_set`, `total_field`, `counterparty_field` из задачи 1, `normalize`.
- Produces:
  - `@dataclass(frozen=True) RemoteField(code: str, title: str)` в `connectors/base.py`.
  - Необязательный метод коннектора `async remote_fields(self, ctx: DestCtx) -> list[RemoteField]`. В
    `BaseConnector` его нет; наличие проверяется через `getattr`.
  - Необязательный метод `suggest_mapping(self, schema: DocSchema, config: dict) -> dict[str, str]` — свой
    подбор вместо общего (только 1С).
  - `fieldmatch.SYNONYMS: dict[str, tuple[str, ...]]`:
    - `inn: ("инн",)`, `kpp: ("кпп",)`, `bik: ("бик",)`;
    - `total: ("сумма", "итого", "итог", "к оплате", "всего к оплате", "сумма к оплате", "сумма сделки")`;
    - `vat: ("ндс", "сумма ндс", "в т ч ндс")`;
    - `date: ("дата", "дата документа")`, `number: ("номер", "номер документа", "№")`;
    - `party: ("поставщик", "продавец", "контрагент")`.
  - `fieldmatch.SIMILARITY = 0.8`.
  - `fieldmatch.suggest(schema: DocSchema, base: DocSchema, fields: list[RemoteField], *, skip_keys:
    AbstractSet[str] = frozenset(), taken_codes: AbstractSet[str] = frozenset()) -> dict[str, str]` — ключ поля
    документа → code. Кандидаты: нетабличные поля верхнего уровня, кроме `skip_keys`. Поля системы с `code` из
    `taken_codes` не предлагаются.
  - `onec_odata.default_mapping(schema, config) -> dict[str, str]`. Сторона `supplier` при
    `document_kind == "purchase"` (по умолчанию), иначе `buyer`. Пары: `{side: "counterparty", f"{side}_inn":
    "counterparty_inn", f"{side}_kpp": "counterparty_kpp", "number": "incoming_number", "issued":
    "incoming_date"}` плюс `f"{table}.{col}"` → роль из `ITEM_COLUMNS`. Только ключи, которые есть в схеме.
  - `amocrm.MAX_FIELD_PAGES = 20`.
  - API `POST /v1/destinations/{id}/remote-fields?schema={slug}` → `{fields: [{code, title}], suggested: {...}}`.

Алгоритм `suggest`. Три прохода, каждое поле системы и каждое поле документа используется не больше одного раза:
1. **Точное совпадение.** Поля системы по порядку: `normalize(title)` или `normalize(code)` входит в варианты поля
   документа (подпись компании, `title or name` базовой схемы, `name`, `name` с `_`→пробел). Берётся первое
   свободное поле документа в порядке схемы.
2. **Синонимы.** Группа поля системы — синоним, равный `normalize(title)` или стоящий в его начале перед пробелом.
   Синонимы перебираются от длинных к коротким, поэтому «Сумма НДС» попадает в `vat`, а не в `total`. Группы полей
   документа:
   - `inn`/`kpp`/`bik` — по правилу поля;
   - `total` — `total_field`;
   - `vat` — имя `vat`;
   - `date` — `issued`, иначе первая дата;
   - `number` — `number`;
   - `party` — `counterparty_field`, затем `supplier`/`buyer`.
   Берётся первое свободное поле группы.
3. **Похожесть.** `difflib.SequenceMatcher` между `normalize(title)` и вариантами из шага 1, ratio ≥ 0.8. Жадно по
   убыванию ratio, при равенстве — порядок схемы.

Источники полей:
- Google Таблицы: непустые уникальные заголовки строки 1 листа документов, `code == title`. Нет листа или пустая
  строка → `[]`. Листы не создаются.
- Битрикс24: `crm.deal.fields`, ключи по `_USER_FIELD` с префиксом `UF_CRM_`. title — первое непустое из
  `listLabel`, `formLabel`, `title`, иначе код; через `clean`.
- amoCRM: `GET /leads/custom_fields` с `page=N&limit=250`, `_embedded.custom_fields` → `RemoteField(str(id),
  clean(name))`. Дальше, пока есть `_links.next` и страница непустая, не больше `MAX_FIELD_PAGES`.
- 1С: `[RemoteField(role, label) for role, label in ROLES.items()]`, без сети.

Эндпоинт (рядом с `/test`, права `ManagerDep`):
1. Ищется свой получатель (`_owned`), иначе 404.
2. Нет `remote_fields` → 422 ««{label}» не отдаёт список полей».
3. Расшифровка секретов (`_decrypt`). Затем `_check_config` и `_check_urls` по секретам; нарушение → 422 с текстом.
4. `resolver.resolve(..., schema, None)`, `LookupError` → 404. Подписи через `get_label_set`.
5. `httpx.AsyncClient(timeout=HTTP_TIMEOUT)`, затем `remote_fields(ctx)`. `ConnectorError` → 502 с `exc.message`.
   Прочее исключение → 502 «не удалось получить поля системы ({type})» и без текста исключения.
6. `suggested`:
   - у коннектора с `suggest_mapping` — его результат;
   - иначе `fieldmatch.suggest`: `skip_keys` — ключи сохранённого `mapping` с непустым значением, `taken_codes` —
     эти значения.

Сохранённое сопоставление эндпоинт не меняет никогда.

- [ ] **Step 1: тесты `tests/test_fieldmatch.py`** (на `invoice_ru`):
  - `test_exact_by_label_default_and_name`: поля `[RF("1", "Налог"), RF("2", "Поставщик"), RF("3", "supplier_inn")]`
    при подписи `vat` «Налог» → `{"vat": "1", "supplier": "2", "supplier_inn": "3"}`.
  - `test_synonyms`: `[RF("10", "ИНН контрагента"), RF("11", "Сумма НДС"), RF("12", "К оплате")]` →
    `supplier_inn: "10"`, `vat: "11"`, `total: "12"`.
  - `test_similarity_threshold`: «Поставщикк» → supplier. «Пост» → нет.
  - `test_one_to_one_and_skips`: два поля системы «ИНН» → назначено одно. `skip_keys={"vat"}` и
    `taken_codes={"1"}` соблюдаются.
- [ ] **Step 2: тесты коннекторов** (respx, фикстуры файлов):
  - `test_sheets_remote_fields_reads_header` и `test_sheets_remote_fields_no_sheet_empty` (`created == []`).
  - `test_bitrix_remote_fields_user_fields_only`: в ответе `TITLE` и `UF_CRM_INN` (`listLabel` «ИНН») → только
    `[RemoteField("UF_CRM_INN", "ИНН")]`.
  - `test_amocrm_remote_fields_paginates`: 2 страницы → 3 поля. `test_amocrm_remote_fields_page_cap`: `next` есть
    всегда → ровно 20 запросов.
  - `test_onec_remote_fields_and_default_mapping`: роли без запросов. `default_mapping(invoice, {})` содержит
    `supplier→counterparty`, `items.name→item_name`. При `{"document_kind": "sale"}` — `buyer→counterparty`.
- [ ] **Step 3: API-тесты `tests/api/test_destinations_api.py`.** `FakeConnector` получает
  `remote = [RemoteField("amount", "Сумма"), RemoteField("tax", "НДС")]` и `remote_fields`, которая пишет вызов в
  `calls` и бросает `self.error`, если он задан.
  - `test_remote_fields_returns_fields_and_suggestions`: получатель с `mapping {"total": "amount"}` →
    `fields` оба. `suggested == {"vat": "tax"}`: total уже сопоставлен, `amount` занят.
  - `test_remote_fields_keeps_saved_mapping`: после запроса `GET` получателя — `mapping` прежний.
  - `test_remote_fields_connector_error_502`: `ConnectorError("Неверный токен")` → 502, `detail == "Неверный токен"`.
  - `test_remote_fields_unexpected_error_hides_secret`: `RuntimeError("boom SUPER-SECRET")` → 502, `SUPER-SECRET`
    нет в `r.text`.
  - `test_remote_fields_not_supported_422`: получатель `test_notifier` (`fake_notifier`) → 422.
  - `test_remote_fields_operator_403_and_foreign_404`.
  - `test_remote_fields_unknown_schema_404`.
- [ ] **Step 4:** прогнать → FAIL; реализовать; → PASS; весь `pytest -q`, ruff.
- [ ] **Step 5:** коммит `feat(connectors): поля системы из Таблиц, Битрикс24, amoCRM и 1С с автоподбором`.

### Task 6: Кабинет — страница «Поля документов»

**Files:**
- Create: `web/src/app/app/fields/page.tsx`, `web/src/app/app/fields/actions.ts`
- Create: `web/src/components/app/FieldLabelsForm.tsx`
- Modify: `web/src/components/app/AppShell.tsx` (NAV)
- Modify: `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/lib/format.ts`, `web/src/app/app/page.tsx`

**Interfaces:**
- Consumes: API задачи 1 и `summary[].label` из задачи 3.
- Produces:
  - Типы: `FieldLabelRow {key, type, group: string | null, default: string, label: string, queue: boolean}`,
    `FieldLabels {slug, title, version, rows, queue_fields: string[], queue_default: boolean}`,
    `SummaryItem.label: string`.
  - `ApiError.fieldErrors: Record<string, string>` (третий аргумент конструктора, по умолчанию `{}`). Собирается из
    `detail[]`: последний элемент `loc` из `labels` или `"queue_fields"`. `detail` как строка не меняется.
  - `fieldLabel(spec: Pick<SchemaColumn, "name" | "description" | "title">)`: сначала непустой `title`, затем
    прежняя логика (описание, имя).
  - `saveLabels(slug: string, prev: LabelsState, form: FormData): Promise<LabelsState>`, где
    `LabelsState = {saved?: boolean; error?: string; fieldErrors?: Record<string, string>}`.
  - NAV: `{ href: "/app/fields", label: "Поля" }` после «Справочники».

Страница:
- Список типов из `GET /v1/schemas` — ссылки `?schema=<slug>`, текущая с `aria-current`. Без параметра — первый.
- Таблица `FieldLabelsForm`: сначала поля верхнего уровня, затем каждая таблица отдельной группой с заголовком.
- Колонки:
  - «Поле» — `default`, под ним ключ `font-mono text-xs text-muted`;
  - «Ваше название» — `Field`/input `name="label:<key>"`, `maxLength={80}`, placeholder = `default`;
  - «В очереди» — checkbox `name="queue" value=<key>`. Только у нетабличных полей верхнего уровня. Если отмечено
    3, остальные disabled.
- Ошибка поля из `fieldErrors[key]` выводится под его input. Общая — в `role="alert"`. «Сохранено.» — в
  `role="status"`.
- Подсказка (дословно): «Названия появятся в очереди, карточке документа, цифровом PDF, таблицах, Excel и
  уведомлениях. Модель их не видит: распознавание идёт по исходным полям. Пустое поле — название по умолчанию.
  Ничего не отмечено в «В очереди» — показываем контрагента, сумму и дату.»
- Оператор (`me.user?.role !== "owner"`): форма `disabled`, строка «Названия меняет владелец аккаунта.».
- Очередь (`page.tsx`): `dt` показывает `s.label` (без `font-mono`), `key` остаётся `s.field`.

- [ ] **Step 1:** типы, `api.ts`, `format.ts`, очередь. В `actions.ts` ветка ошибок как в `dictionaries/actions.ts`:
  422/404 → `{error: e.detail, fieldErrors: e.fieldErrors}`. Прочее → общий текст «Не удалось сохранить
  названия: сервис недоступен. Попробуйте ещё раз.»
- [ ] **Step 2:** страница, форма, NAV.
- [ ] **Step 3:** `npm run lint` и `npm run build` в `web/` → без ошибок.
- [ ] **Step 4:** ручная проверка на `npm run dev` с локальным API (как в README). Переименовать `vat` → после
  сохранения видно в карточке документа, форме проверки, правилах и сопоставлении подключения. 81 символ → ошибка
  у поля. Четвёртая галочка недоступна.
- [ ] **Step 5:** коммит `feat(web): страница «Поля документов» и названия компании в кабинете`.

### Task 7: Кабинет — «Подтянуть поля» в форме подключения

**Files:**
- Modify: `web/src/app/app/integrations/actions.ts`
- Modify: `web/src/components/app/DestinationForm.tsx`
- Modify: `web/src/lib/types.ts`

**Interfaces:**
- Consumes: эндпоинт задачи 5.
- Produces:
  - Типы: `RemoteField {code, title}`, `RemoteFieldsResult = {ok: true; fields: RemoteField[]; suggested:
    Record<string, string>} | {ok: false; message: string}`.
  - `fetchRemoteFields(id: string, slug: string): Promise<RemoteFieldsResult>` — server action. `ApiError` →
    `{ok: false, message: e.detail}`. Прочее → «Не удалось получить поля: сервис недоступен.»

Поведение формы:
- Кнопка «Подтянуть поля из {connector.label}» (`Button variant="ghost"`) — только при `destination` и
  `hasMapping(connector)`. Над списком сопоставления подпись: «Берём поля по сохранённым настройкам
  подключения.»
- После успеха — `remote` в состоянии формы:
  - у текстовых полей (`FREE_TARGET`) появляется `list=<datalist id>` с `<option value={code}>{title}</option>`;
    ручной ввод остаётся;
  - у 1С селект и так из `target_fields`, меняется только заполнение.
- Предложения применяются только к строкам текущей схемы с пустым значением. У Таблиц пустая строка — это поле не
  отмечено или отмечено без названия; при подстановке оно отмечается. Заполненные строки не трогаются.
- Статус `role="status"`: «Подобрано {n} из {всего строк}; полей в {label}: {fields.length}.» Ошибка — в
  `role="alert"` с текстом сервера.
- Предложения запоминают `slug`. При смене схемы старые не применяются, нужно нажать кнопку снова. Список полей
  системы остаётся.
- Без нажатия форма работает как сейчас (anti-goal «старые подключения ведут себя иначе»).

- [ ] **Step 1:** реализация.
- [ ] **Step 2:** `npm run lint`, `npm run build` → без ошибок.
- [ ] **Step 3:** ручная проверка на получателе 1С (подбор без сети, настоящая система не нужна): кнопка
  заполняет пустые роли, заполненную вручную не меняет, счётчик верный. Ошибка сервера (например, получатель
  Таблиц с битым ключом) показывается в `role="alert"`.
- [ ] **Step 4:** коммит `feat(web): подтягивание полей системы и автоподбор в форме подключения`.

### Task 8: Живая проверка на демо-стенде

**Files:** изменений кода нет. Найденные дефекты чинятся в файлах их задач отдельными коммитами `fix: …`.

- [ ] **Step 1:** поднять стенд и демо:
  `docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build`, затем `… run --rm worker docflow
  seed-demo --reset`. Сервис `migrate` применяет `0010` и `seed-schemas`; в его логе `обновлено: 1` (УПД).
  Очистить Mailpit: `curl -X DELETE http://localhost:8025/api/v1/messages`. Логин и пароль из вывода не
  сохранять в файлы и не печатать в отчёт.
- [ ] **Step 2:** в браузере (Playwright MCP) войти владельцем:
  - на `/app/fields` для «Счёт» переименовать `total` → «К оплате», в очередь — `total` и `vat`;
  - очередь показывает «К оплате» и НДС, контрагента нет;
  - карточка документа и форма проверки показывают «К оплате»;
  - цифровой PDF (`/app/files/<id>/digital`) содержит «К оплате».
- [ ] **Step 3:** одобрить документ `needs_review` типа «Счёт», который правила демо после одобрения уведомляют на
  почту бухгалтерии (`mail_accounting`) → в Mailpit письмо «Новый документ» со строкой «…, К оплате: …». Какой документ
  подходит, видно по правилам в `src/docflow/demo/data.py` (`to_accounting`). У одобренного документа в очереди
  нет замечания.
- [ ] **Step 4:** у почты и Telegram кнопки «Подтянуть поля» нет; у получателя 1С (если есть в демо) кнопка
  подбирает роли. УПД в карточке: НДС подписан «НДС».
- [ ] **Step 5:** `pytest -q`, `ruff check src tests`, `pytest -m integration tests/db` (compose Postgres 55432)
  → зелёные. Коротко записать итог в отчёт задачи, без паролей и ключей.
