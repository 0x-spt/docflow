# Конструктор полей и типов документов: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** компания в кабинете сама добавляет, убирает и меняет поля типа документа, правила сверки и заводит свои
типы. Сохранение — новая версия через `POST /v1/schemas`.

**Architecture:**
- Бэкенд почти готов. Добавляем:
  - `expected_version` у `POST /v1/schemas` (409, если версия ушла);
  - чистку подписей и очереди от удалённых полей в той же транзакции;
  - `loc` до поля или правила в 422 (`SchemaPathError` + обработчик `RequestValidationError`);
  - `?builtin=true` у `GET /v1/schemas/{slug}` для отката к стандартному;
  - POST только владельцу или ключу API.
- Веб: чистые модули в `web/src/lib/field-builder/` (транслит, модель редактора ⇄ `DocSchema`, фразы правил,
  сводка изменений, разнос ошибок 422). Тесты к ним — встроенный `node --test`, без новых зависимостей.
- Клиентский `SchemaEditor` + server actions в `web/src/app/app/fields/actions.ts`. Страницы
  `/app/fields/new` и `/app/fields/[slug]/edit`.

**Tech Stack:** Python 3.12, FastAPI 0.162, Pydantic 2.14, SQLAlchemy async, pytest (SQLite в `tests/api`),
Next.js 15 / React 19, Node 24 (`node:test`, type stripping).

**Spec:** `docs/superpowers/specs/2026-10-30-field-builder-design.md` (предыдущая:
`docs/superpowers/specs/2026-10-29-field-names-design.md`)

## Global Constraints

- Коммиты без `Co-Authored-By` и без «Generated with Claude Code».
- Только синтетические данные. Реквизиты брать из уже существующих тестов и `tests/e2e/fake_responses.json`.
- Секреты, пароли демо и ключи API не печатать в отчёты и не сохранять в файлы.
- Стиль и плотность комментариев как в окружающем коде. Комментарии и тексты для людей по-русски, идентификаторы
  по-английски. `ruff check src tests` чист (line-length 120).
- Существующие тесты остаются зелёными. Меняются только тесты, названные в задаче.
- Команды:
  - `.venv/Scripts/python -m pytest -q`, `.venv/Scripts/ruff check src tests`;
  - в `web/`: `npm test`, `npm run lint`, `npm run build`. Кэш npm остаётся на E:.
- Postgres — только compose на `127.0.0.1:55432`. Нативный `127.0.0.1:5432` не трогать.
- Формат тела схемы не меняется. Изменения API только добавляются: query `expected_version`, query `builtin`,
  403 оператору на POST. `DELETE` для схем нет.
- Служебное имя (`name`) существующего поля никогда не меняется редактором. Slug — `^[a-z0-9_]{2,40}$`.
- Модель видит описание схемы (`description`), а не подписи компании. Редактор грузит схему только с `raw=true`.
- Таблица в типе одна, у колонок нет проверок, вложенных таблиц нет.
- Типы полей (подпись → значение): Текст `string`, Число `number`, Целое число `integer`, Дата `date`, Да/нет
  `boolean`, Выбор из списка `enum`, Таблица `table`. У колонок все, кроме Таблицы.
- Проверки (подпись → `kind`): нет, ИНН `inn`, КПП `kpp`, БИК `bik`, ОГРН `ogrn`, СНИЛС `snils`, Справочник
  `dictionary` (`params.name`), Шаблон `regex` (`params.pattern`). Флаг «Дата не в будущем» — `date_not_future`.

## Review Focus

1. **Название нового поля правят уже после того, как на него сослалось правило документа.** Ожидание: правило
   следует за новым служебным именем, а не ссылается на старое. Тест: задача 3,
   `withNames rewrites doc rule refs of renamed new fields`.
2. **Два открытых редактора, или новый тип со slug, который уже занят (в том числе встроенным `invoice_ru`).**
   Ожидание: 409 с просьбой обновить страницу или сменить имя, а не тихая перезапись или перекрытие встроенной.
   Тесты: задача 1, `test_post_expected_version_stale_409`, `test_post_expected_version_zero_rejects_existing`.
3. **Встроенный тип открыли и сохранили без правок.** Ожидание: схема бит в бит та же, сводка «изменений нет»,
   новая версия не создаётся. Тесты: задача 3, `round-trip keeps builtin schemas intact`; задача 2,
   `changeSummary: no changes`.
4. **Сервер вернул 422 без `loc` или с `loc` до колонки или правила поля.** Ожидание: ошибка видна у той
   колонки, того поля или того правила, иначе общим сообщением. Тесты: задача 1, `test_post_422_*`; задача 2,
   `errorTargets …`.
5. **Название без кириллицы и латиницы, очень длинное или повторяет соседнее.** Пример: «№», «2-НДФЛ»,
   80 символов, два поля «Сумма». Ожидание: служебное имя валидно, уникально в схеме и ≤ 40 символов. Тест:
   задача 2, `uniqueName / toIdentifier edge cases`.

---

### Task 1: Бэкенд — версия, чистка подписей, `loc` в 422, откат к встроенной

**Files:**
- Modify: `src/docflow/schemas/model.py` (`SchemaPathError`, `_first_duplicate`, `DocSchema._validate`)
- Modify: `src/docflow/api/app.py` (обработчик `RequestValidationError`)
- Modify: `src/docflow/api/schemas.py` (`create_schema`, `get_schema`, описания эндпоинтов)
- Modify: `src/docflow/labels.py` (`prune_label_set`)
- Test: `tests/schemas/test_model.py`, `tests/api/test_schemas.py`, `tests/api/test_field_labels_api.py`

**Interfaces:**
- Produces:
  - `class SchemaPathError(ValueError)`, `__init__(self, loc: tuple[str | int, ...], message: str)`, атрибут
    `loc`. Пути: `("fields", i)`, `("fields", i, "columns", j)`, `("fields", i, "rules", k)`, `("rules", j)`.
    Тексты сообщений прежние.
  - 422 от `POST /v1/schemas`: `detail[].loc` = `["body", *SchemaPathError.loc]`. Ошибки валидаторов
    `FieldSpec`/`ColumnSpec` и так приходят с `["body","fields",i(,"columns",j)]`.
  - `POST /v1/schemas?expected_version=N` (`int ≥ 0`, необязателен). `N` сравнивается с версией, которую отдаёт
    `GET /v1/schemas/{slug}?raw=true` (последняя по `SchemaResolver`). Если схемы нет, версия 0. Расхождение →
    409.
  - `GET /v1/schemas/{slug}?raw=true&builtin=true` — встроенная схема без перекрытия тенанта
    (`resolver.resolve(session, None, slug, version)`), иначе 404.
  - `async def prune_label_set(session: AsyncSession, tenant_id: uuid.UUID, schema: DocSchema) -> None` — без
    commit.

- [ ] **Step 1: Написать падающие тесты**

  `tests/schemas/test_model.py`:
  - `test_schema_path_error_points_to_doc_rule`: правило документа
    `account_matches_bik {account: "number", bik: "nope"}` → `ValidationError`. У
    `errors()[0]["ctx"]["error"]` `.loc == ("rules", 0)`, `"«nope»"` в `str`.
  - `test_schema_path_error_points_to_field_rule_and_duplicate`: `regex {pattern: "("}` у поля №1 → loc
    `("fields", 1, "rules", 0)`. Поля `number, a, number` → loc `("fields", 2)`.

  `tests/api/test_schemas.py` (POST с `headers=auth`, ключ API):
  - `test_post_422_points_to_rule`: `detail[0]["loc"] == ["body", "rules", 0]`, `"«nope»" in detail[0]["msg"]`.
  - `test_post_422_points_to_field_rule`: `["body", "fields", 1, "rules", 0]`.
  - `test_post_expected_version_matches_creates`: `GET /v1/schemas/invoice_ru?raw=true` → `version == 1`.
    POST тела встроенного счёта с `?expected_version=1` → 201, `version == 2`.
  - `test_post_expected_version_stale_409`: тот же POST дважды. Второй → 409, `"обновите страницу" in detail`.
  - `test_post_expected_version_zero_rejects_existing`: `MY_SCHEMA` с `expected_version=0` → 201; повтор → 409,
    `"уже есть" in detail`. Тело `invoice_ru` с `expected_version=0` → 409. Встроенная у других тенантов не
    меняется.
  - `test_operator_cannot_post_schema`: `session_for(api, auth, role="operator")` → 403.
  - `test_get_builtin_ignores_override`: перекрыть `invoice_ru` с title «Наш счёт».
    `GET …/invoice_ru?raw=true&builtin=true` → title встроенной, `version == 1`. `GET …/my_doc?builtin=true` →
    404.

  `tests/api/test_field_labels_api.py`:
  - `test_save_schema_prunes_removed_labels`:
    - `my_doc` v1 с полями `number`, `note`, `amount` (`number`);
    - PUT labels `{"note": "Примечание", "amount": "Сумма"}`, queue `["amount", "note"]`;
    - POST v2 без `note` → в строке `FieldLabels` `labels == {"amount": "Сумма"}`, `queue_fields == ["amount"]`;
    - GET labels: у `amount` подпись «Сумма», ключа `note` нет;
    - PUT `{"labels": {"note": "x"}}` → 422, `loc == ["body", "labels", "note"]`.

- [ ] **Step 2:** `.venv/Scripts/python -m pytest -q tests/schemas/test_model.py tests/api/test_schemas.py tests/api/test_field_labels_api.py` → новые тесты FAIL.

- [ ] **Step 3: Реализация**
  - `model.py`:
    - `_check_columns` заменить на `_first_duplicate(names: list[str]) -> int | None` — индекс первого
      повтора.
    - `_validate` бросает `SchemaPathError` с путями из Interfaces: оборачивает `_check_field_rule` /
      `_check_doc_rule` в `try/except ValueError` и бросает ошибку с тем же `str(e)` (`from e`).
  - `app.py`: `async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse`.
    - Каждой ошибке, у которой `ctx["error"]` — `SchemaPathError`, дописывает его `loc` к `err["loc"]`.
    - Затем отдаёт `fastapi.exception_handlers.request_validation_exception_handler` с новым
      `RequestValidationError(errors, body=exc.body)`.
    - Регистрация: `app.add_exception_handler(RequestValidationError, _validation_error)`.
  - `schemas.py` `create_schema`:
    - параметры `manager: ManagerDep`, `resolver: ResolverDep`, `expected_version: int | None = Query(None, ge=0)`;
    - в каждой попытке цикла до `session.add`: если `expected_version is not None`, сравнить с текущей версией
      (`LookupError` → 0);
    - тексты 409:
      - `expected_version == 0`, а схема есть: `Тип документа «{slug}» уже есть: выберите другое служебное имя`;
      - иначе: `Тип документа изменили после открытия редактора (сейчас версия {current}): обновите страницу`;
    - перед `commit` вызвать `prune_label_set(session, tenant_id, stored)`. При `IntegrityError` откат снимает и
      чистку, следующая попытка повторяет её.
    - `description` эндпоинта дополнить про `expected_version` и 403 оператору.
  - `get_schema`: `builtin: bool = Query(False, description="Встроенная схема без перекрытия тенанта")`. Работает
    и без `raw`: подписи к встроенной тоже накладываются, если не `raw`.
  - `labels.py` `prune_label_set`:
    - оставляет в `labels` только `label_keys(schema)`, в `queue_fields` — только нетабличные поля верхнего уровня;
    - нет строки — ничего не делает.

- [ ] **Step 4:** весь `pytest -q` и `ruff check src tests` → зелёные (старые `test_post_invalid_schema_422`,
  `test_model.py` не меняются).

- [ ] **Step 5:** коммит `feat(api): версия при сохранении схемы, чистка подписей, путь ошибки до поля`.

### Task 2: Веб — тест-раннер и чистые функции: транслит, правила, сводка, ошибки

**Files:**
- Modify: `web/package.json` (`"test": "node --test \"tests/unit/**/*.test.ts\""`), `web/tsconfig.json`
  (`"allowImportingTsExtensions": true`)
- Modify: `web/src/lib/types.ts` (`RuleSpec`, `rules?` у `SchemaField` и `DocSchema`, `ValidationItem`)
- Create: `web/src/lib/field-builder/translit.ts`, `rules.ts`, `summary.ts`, `errors.ts`
- Create: `web/tests/unit/fixtures/builtin.json` — `{slug: model_dump}` трёх встроенных схем:
  `.venv/Scripts/python -X utf8 -c "import json;from docflow.schemas.library import builtin_schemas as b;print(json.dumps({k:v.model_dump(mode='json') for k,v in b().items()},ensure_ascii=False,indent=1))" > web/tests/unit/fixtures/builtin.json`.
  Тесты читают её через `readFileSync` + `JSON.parse`.
- Test: `web/tests/unit/translit.test.ts`, `rules.test.ts`, `summary.test.ts`, `errors.test.ts`
- Test: `tests/schemas/test_library.py::test_web_fixture_matches_builtins` — фикстура равна текущему дампу. Иначе
  тест падает с подсказкой команды выше.

Правило модулей `field-builder`: из `../types` только `import type`. Соседние модули импортируются с явным `.ts`
(`./rules.ts`). Так их без сборки запускает `node --test` (Node 24 сам срезает типы). Никаких `enum`/`namespace`.

**Interfaces:**
- Produces (`types.ts`):
  - `interface RuleSpec { kind: string; params: Record<string, unknown> }`; `SchemaField.rules?: RuleSpec[]`;
    `DocSchema.rules?: RuleSpec[]`;
  - `interface ValidationItem { loc: (string | number)[]; msg: string }`.
- Produces (`translit.ts`):
  - `translit(text: string): string` — таблица: а a, б b, в v, г g, д d, е e, ё e, ж zh, з z, и i, й y, к k,
    л l, м m, н n, о o, п p, р r, с s, т t, у u, ф f, х kh, ц ts, ч ch, ш sh, щ shch, ъ —, ы y, ь —, э e,
    ю yu, я ya. Регистр сбрасывается.
  - `toIdentifier(text: string, maxLen = 40): string`:
    - транслит, затем всё вне `[a-z0-9]` → `_`, повторы `_` схлопнуть, `_` по краям убрать;
    - обрезать до `maxLen` и снова убрать хвостовой `_`;
    - пусто → `"field"`.
  - `uniqueName(base: string, taken: ReadonlySet<string>, maxLen = 40): string`:
    - `base`, иначе `base_2`, `base_3`…;
    - основа обрезается так, чтобы с суффиксом влезть в `maxLen`.
  - `slugFromTitle(title: string): string` — `toIdentifier(title)`. Пусто → `"doc"`, один символ → `"<c>_doc"`.
- Produces (`rules.ts`):
  - `DOC_RULE_PARAMS: Record<string, Record<string, "field" | "table" | "column" | "int">>` — копия
    `_DOC_RULE_PARAMS` из `model.py` (комментарий: «держать в согласии с docflow/schemas/model.py»);
  - `ruleRefs(rule: RuleSpec): string[]`:
    - ключи полей и таблицы как есть, колонки как `"<table>.<column>"`;
    - пустые/необязательные параметры пропускаются, неизвестный `kind` → `[]`;
  - `rulesUsing(rules: RuleSpec[], key: string): number[]` — индексы правил, ссылающихся на `key`. Для таблицы
    `key = "items"` — и на её колонки;
  - `rulePhrase(rule: RuleSpec, titleOf: (key: string) => string): string` — тексты ниже;
  - конструкторы:
    - `sumRule(table, column, total)` → `{kind: "table_sum_equals", params: {table, column, total, tolerance: 0.01}}`;
    - `rowProductRule(table, qty, price, amount)` → `{kind: "table_row_product", params: {table, qty, price, amount}}`;
    - `accountBikRule(account, bik)` → `{kind: "account_matches_bik", params: {account, bik}}`.
- Produces (`summary.ts`):
  - `plural(n: number, forms: [string, string, string]): string`;
  - `changeSummary(before: DocSchema | null, after: DocSchema): string`.
- Produces (`errors.ts`): `errorTargets(items: ValidationItem[], sent: DocSchema): Record<string, string>`.
  - Ключи: `"fields.<i>"`, `"fields.<i>.columns.<j>"`, `"rules.<j>"`, `"general"`. Ключ правила поля — его поле.
  - Префикс `Value error, ` срезается. Несколько ошибок на один ключ — через `"; "`.
  - Без пути (`loc` = `["body"]` или пусто):
    - текст начинается с `<kind>:` → первое правило документа этого `kind`;
    - иначе первое `«x»`, где `x` — `name` поля из `sent` → это поле;
    - иначе `general`.

Фразы `rulePhrase` (`T(k)` = `titleOf(k)`, колонка — `T("items.amount")`):
- `table_sum_equals` → `Сумма колонки «T(table.column)» = «T(total)»`
- `table_row_product` → `«T(qty)» × «T(price)» = «T(amount)» в каждой строке`
- `table_product_sum` → `Сумма «T(qty)» × «T(price)» по строкам = «T(total)»`
- `account_matches_bik` → `Счёт «T(account)» сходится с БИК «T(bik)»`
- `total_consistent`:
  - `«T(total)» = «T(subtotal)» + «T(vat)»`;
  - без `subtotal`, но с таблицей — `сумма колонки «T(table.column)»` вместо подытога;
  - без `vat` — без `+ …`;
- `date_order` → `«T(earlier)» не позже «T(later)»`
- `date_within` → `«T(later)» не позже чем через {max_days} дн. после «T(earlier)»`
- прочее → `Правило {kind}`

- [ ] **Step 1: Написать падающие тесты** (`node:test` + `node:assert/strict`)
  - `translit.test.ts`:
    - `toIdentifier("Номер договора") === "nomer_dogovora"`, `toIdentifier("Пробег, км") === "probeg_km"`,
      `toIdentifier("Объём щебня") === "obem_shchebnya"`, `toIdentifier("ИНН") === "inn"`;
    - `uniqueName / toIdentifier edge cases`:
      - `toIdentifier("№") === "field"`, `toIdentifier("2-НДФЛ") === "2_ndfl"`;
      - `toIdentifier("а".repeat(80)).length === 40`;
      - `uniqueName("summa", new Set(["summa"])) === "summa_2"`;
      - `uniqueName("x".repeat(40), new Set(["x".repeat(40)]))` — длина 40, оканчивается на `_2`;
    - `slugFromTitle("Путевой лист") === "putevoy_list"`, `slugFromTitle("Я") === "ya"`,
      `slugFromTitle("!") === "doc"`.
  - `rules.test.ts` (схема — `invoice_ru` из фикстуры):
    - `ruleRefs(table_sum_equals счёта)` deepEqual `["items", "items.amount", "subtotal"]`;
    - `rulesUsing(invoice.rules, "vat")` deepEqual `[3]`;
    - `rulesUsing(invoice.rules, "items")` deepEqual `[0, 1, 3]`;
    - `rulesUsing(invoice.rules, "items.unit")` deepEqual `[]`;
    - фраза `table_sum_equals` с `titleOf` по `title` → `Сумма колонки «Сумма» = «Итого без НДС»`;
    - `total_consistent` без `subtotal` и `vat` →
      `«Всего к оплате» = сумма колонки «Сумма»`;
    - неизвестный `kind` → `Правило foo`;
    - `sumRule("items", "amount", "total")` deepEqual ожидаемому литералу.
  - `summary.test.ts`:
    - `changeSummary: no changes` — та же схема → `"изменений нет"`;
    - +2 поля, −1, тип изменён у 1 → `"добавлено 2 поля, удалено 1, тип изменён у 1"`;
    - только −1 → `"удалено 1 поле"`;
    - добавлено правило и удалено правило → `"добавлено правил: 1, удалено правил: 1"`;
    - правки только title/description → `"изменены названия и подсказки"`;
    - `before = null`, 3 поля → `"новый тип документа: 3 поля"`;
    - `plural(5, ["поле", "поля", "полей"]) === "полей"`, `plural(21, …) === "поле"`, `plural(12, …) === "полей"`.
  - `errors.test.ts`:
    - `errorTargets` с `loc ["body","fields",2,"columns",1]` → ключ `"fields.2.columns.1"`;
    - `["body","fields",1,"rules",0]` → `"fields.1"`;
    - `["body","rules",0]` → `"rules.0"`;
    - `loc ["body"]` и `"Value error, «supplier»: pattern не компилируется"` → `"fields.<индекс supplier>"`, текст
      без префикса;
    - `"Value error, account_matches_bik: bik=«x» — нет такого поля"` → `"rules.2"` (индекс в счёте);
    - непривязанное → `general`.

- [ ] **Step 2:** в `web/`: `npm test` → FAIL (модулей нет).
- [ ] **Step 3:** создать фикстуру, реализовать модули и типы.
- [ ] **Step 4:** `npm test`, `npm run lint`, `npm run build`, `pytest -q tests/schemas` → зелёные.
- [ ] **Step 5:** коммит `feat(web): чистые функции конструктора полей и node --test`.

### Task 3: Веб — модель редактора ⇄ `DocSchema`

**Files:**
- Create: `web/src/lib/field-builder/model.ts`
- Test: `web/tests/unit/model.test.ts` (фикстура `builtin.json` из задачи 2)

**Interfaces:**
- Consumes: `toIdentifier`, `uniqueName`, `slugFromTitle` (задача 2), `ruleRefs`, `rulesUsing`, `rulePhrase`.
- Produces:

```ts
export type CheckKind = "none" | "inn" | "kpp" | "bik" | "ogrn" | "snils" | "dictionary" | "regex";
export interface EditorColumn {
  uid: string; name: string; isNew: boolean; title: string; description: string;
  type: ColumnType; values: string; // варианты enum, по одному в строке
  base: SchemaColumn | null;        // как пришло с сервера: для сохранения нетронутого
}
export interface EditorField extends Omit<EditorColumn, "type" | "base"> {
  type: FieldType; required: boolean; check: CheckKind; checkParam: string; notFuture: boolean;
  columns: EditorColumn[]; base: SchemaField | null;
}
export interface EditorDoc {
  slug: string; isNew: boolean; title: string; description: string; version: number; // 0 — новый тип
  fields: EditorField[]; rules: RuleSpec[]; seq: number; // счётчик uid новых строк
}
export const TYPE_OPTIONS: { value: FieldType; label: string }[];   // Global Constraints, по порядку
export const CHECK_OPTIONS: { value: CheckKind; label: string }[];
export function fromSchema(schema: DocSchema): EditorDoc;
export function newDoc(): EditorDoc;
export function newField(doc: EditorDoc): [EditorDoc, EditorField];  // uid `n${seq}`, seq+1
export function newColumn(doc: EditorDoc): [EditorDoc, EditorColumn];
export function withNames(doc: EditorDoc): EditorDoc;
export function toSchema(doc: EditorDoc): DocSchema;
export function validateDoc(doc: EditorDoc, takenSlugs: readonly string[]): Record<string, string>;
export function deleteBlockers(doc: EditorDoc, fieldIndex: number, columnIndex?: number): string[]; // фразы правил
export function titleResolver(doc: EditorDoc, labels: Record<string, string>): (key: string) => string;
export function move<T>(list: readonly T[], index: number, delta: -1 | 1): T[];
```

Решения внутри:
- uid детерминированы: `fromSchema` даёт `f<i>` и `f<i>c<j>`, новые строки — `n<seq>`. Совпадают при SSR и
  гидрации, на них строятся `id` полей ввода.
- `newDoc()`: `slug: ""`, `version: 0`, поля:
  - `number` — «Номер документа», `string`, required, описание «Номер документа строкой, как написан.»;
  - `issued` — «Дата документа», `date`, required, `notFuture`, описание «Дата документа. null, если даты нет.»;
  - обоим `isNew: false`, имена фиксированы.
- `withNames`: имена существующих полей и колонок не трогает. Новым присваивает
  `uniqueName(toIdentifier(title), занятые)` по порядку: поля — по схеме, колонки — внутри своей таблицы. Если
  имя нового поля или колонки сменилось, переписывает ссылки в `doc.rules` (и в параметрах-колонках).
- `toSchema` выдаёт полную форму `model_dump`:
  - поле — `{name, type, title, description, values, required, columns, rules}`;
  - колонка — `{name, type, title, description, values}`;
  - пустой `title` → `null` (только у старых строк, у новых он обязателен);
  - у нового поля пустая подсказка → `description = title` (модель не видит `title`, ей нужно человеческое
    имя);
  - `values` — только у `enum`: строки trim, без пустых и повторов, иначе `null`;
  - `columns` — только у `table`;
  - `rules` поля:
    - порядок `base.rules` сохраняется;
    - первая проверка из `CHECK_OPTIONS` заменяется на текущую на том же месте; при том же `kind` параметры
      сливаются, `fuzzy` справочника остаётся;
    - `date_not_future` — только если `notFuture` и тип `date`;
    - прочие правила (`date_not_older`, вторая проверка) — как были;
    - новая проверка, если места не нашлось, — в конец;
    - параметры: `regex` → `{pattern}`, `dictionary` → `{name}`, прочие → `{}`;
  - у `table` проверки нет.
- `validateDoc` — ключи как у `errorTargets` плюс `title`, `slug`, `fields`, `fields.<i>.title|values|columns|check|type`,
  `fields.<i>.columns.<j>.title|values`. Тексты:
  - `title`: «Введите название типа»;
  - `slug` (только `isNew`): «Служебное имя: 2–40 символов, латинские строчные буквы, цифры и «_»». Занят
    (`takenSlugs`) — «Тип с таким служебным именем уже есть»;
  - `fields`: «Добавьте хотя бы одно поле»;
  - название нового поля — «Введите название поля», колонки — «Введите название колонки»;
  - `enum` без вариантов — «Добавьте хотя бы один вариант»;
  - `table` без колонок — «Добавьте хотя бы одну колонку»;
  - вторая таблица (`.type`) — «Таблица в типе может быть только одна»;
  - справочник не выбран — «Выберите справочник», пустой шаблон — «Введите шаблон»;
  - `rules.<j>`: ссылка из `ruleRefs` не находится в `toSchema(doc)` (или `table` уже не таблица) —
    «Правило ссылается на то, чего уже нет: «x»».
- `deleteBlockers`: фразы правил из `rulesUsing` по ключу поля или `table.column`.
- `titleResolver`: подпись компании → `title` → `name`. Ключ колонки — `table.column`.

- [ ] **Step 1: Написать падающие тесты** (`model.test.ts`)
  - `round-trip keeps builtin schemas intact`: для каждой схемы фикстуры
    `deepStrictEqual(toSchema(fromSchema(s)), s)`.
  - `newDoc has number and issued`: `toSchema(newDoc())` → имена `["number", "issued"]`. У `issued` `rules`
    deepEqual `[{kind: "date_not_future", params: {}}]`.
  - `new field gets translit name and title as description`: поле «Номер договора» с пустой подсказкой →
    `name === "nomer_dogovora"`, `description === "Номер договора"`. Второе такое же → `nomer_dogovora_2`.
  - `existing names never change`: у `supplier` счёта сменить title на «Продавец» → `name === "supplier"`.
  - `withNames rewrites doc rule refs of renamed new fields`: новое поле «Итого» → `itogo`, добавлен
    `sumRule("items","amount","itogo")`. Title → «Итого к оплате» → `params.total === "itogo_k_oplate"`.
  - `check replaces in place and keeps params`:
    - `supplier_inn` счёта: `inn` → `regex` с `\d{10}` → `rules` deepEqual
      `[{kind:"regex",params:{pattern:"\\d{10}"}}]`;
    - `check = "none"` → `[]`;
    - тип `issued` → `string` → `date_not_future` пропадает.
  - `validateDoc flags table type change referenced by rule`: у счёта `items.type = "string"` → есть ключи
    `rules.0` и `rules.1`.
  - `validateDoc messages`:
    - пустой новый тип → `title` и `slug` с текстами выше;
    - `takenSlugs: ["invoice_ru"]` при slug `invoice_ru` → «Тип с таким служебным именем уже есть»;
    - вторая таблица → `fields.<i>.type`.
  - `deleteBlockers`: счёт, поле `vat` → `["«Всего к оплате» = «Итого без НДС» + «НДС»"]`. Колонка
    `items.unit` → `[]`.
  - `move` на краях списка — без изменений.

- [ ] **Step 2:** `npm test` → FAIL.
- [ ] **Step 3:** реализовать `model.ts`.
- [ ] **Step 4:** `npm test`, `npm run lint`, `npm run build` → зелёные.
- [ ] **Step 5:** коммит `feat(web): модель редактора типа документа`.

### Task 4: Редактор — шапка, поля, колонки, проверки, сохранение; страница правки

**Files:**
- Create: `web/src/components/app/SchemaEditor.tsx` (`"use client"`), `web/src/components/app/SchemaFieldCard.tsx`
- Create: `web/src/app/app/fields/[slug]/edit/page.tsx`
- Modify: `web/src/app/app/fields/actions.ts` (`saveSchema`), `web/src/lib/api.ts` (`ApiError.errors`)

**Interfaces:**
- Consumes: `model.ts` (задача 3), `changeSummary`, `errorTargets` (задача 2), API задачи 1.
- Produces:
  - `ApiError` — четвёртый аргумент `errors: ValidationItem[] = []` из `detail[]` (`loc` и `msg`). Строковый
    `detail` → `[]`.
  - `export type SchemaSaveState = { error?: string; errors?: Record<string, string> };`
  - `saveSchema(schema: DocSchema, expectedVersion: number): Promise<SchemaSaveState>` — server action.
    - Проверяет slug (`^[a-z0-9_]{2,40}$`) и `Array.isArray(schema.fields)`.
    - `POST /v1/schemas?expected_version=N`. Успех → `revalidatePath("/app/fields")` и
      `redirect(/app/fields?schema=<slug>&saved=<version>)`.
    - 422 → `errors = errorTargets(e.errors, schema)`, `error = errors.general ?? «Сервер не принял схему:
      исправьте отмеченное.»`.
    - 409/404 → `{error: e.detail}`. 403 → «Поля меняет владелец аккаунта.».
    - Прочее → `console.error` + «Не удалось сохранить: сервис недоступен. Попробуйте ещё раз.».
    - `unstable_rethrow` первым в `catch`, как в соседних actions.
  - `SchemaEditor` props: `{ initial: DocSchema | null; labels: Record<string, string>; dictionaries:
    { name: string; title: string }[]; takenSlugs: string[] }`. `initial = null` — новый тип.
  - `SchemaFieldCard` props: `{ field: EditorField; index: number; count: number; errors: Record<string, string>;
    companyLabel: (key: string) => string | undefined; dictionaries; tableTaken: boolean; onChange(f: EditorField);
    onMove(delta: -1 | 1); onDelete(); onAddColumn(); blockers: string[] }`.

Поведение:
- Состояние — `EditorDoc` в `useState(() => initial ? fromSchema(initial) : newDoc())`. Каждая правка проходит
  через `withNames`. Отправка через `useTransition`, как в `RulesEditor`.
- Шапка:
  - «Название» (`Field`);
  - «Описание — что это за документ (его видит модель)» (`textarea`);
  - у нового типа «Служебное имя» — подставляется `slugFromTitle(title)`, пока его не правили руками.
- Карточка поля:
  - «Название» — title, плейсхолдер `name`;
  - под ним `name` (`font-mono text-xs text-muted`, как на «Полях»);
  - если есть подпись компании: «Название компании: «…» — главнее, меняется на странице «Поля»»;
  - «Подсказка для модели» (`textarea rows=2`), «Тип» (`select`, «Таблица» disabled при `tableTaken`),
    «Обязательное»;
  - «Проверка» — не у таблицы. «Справочник» — select справочников, пустой список → «Справочников нет» и
    `disabled`. «Шаблон» — `input`;
  - «Дата не в будущем» — только при `date`;
  - «Варианты (по одному в строке)» — только при `enum`;
  - кнопки «Выше»/«Ниже» (`disabled` на краях, `aria-label` с названием поля), «Удалить поле»;
  - у `table` — колонки той же разметкой (название, подсказка, тип без Таблицы, варианты), «Выше»/«Ниже»/«Удалить
    колонку», «Добавить колонку».
- Удаление:
  - `blockers` не пуст → не удаляем, `role="alert"` у карточки: «Поле используется в правиле «{фраза}».
    Сначала удалите правило.»;
  - иначе `window.confirm(«Удалить поле «{title}»?»)`. Для `number`/`issued` добавить « Без него документ в
    очереди и выгрузках будет без номера.» / «…без даты.». Колонки — так же.
- Ошибки: `validateDoc` при нажатии «Сохранить» и ответ `saveSchema` → общий словарь ошибок по ключам. Ошибка
  поля — под его input (`Field.error`), `fields.<i>` / `rules.<j>` — `role="alert"` в карточке. Общая — блок
  `role="alert"` наверху с фокусом, как в `FieldLabelsForm`. Есть ошибки валидации → POST не отправляется.
- Сводка `changeSummary(initial, toSchema(doc))` — живая строка `role="status"` у кнопки «Сохранить».
  «изменений нет» → кнопка `disabled`. Есть изменения → `beforeunload` предупреждает о потере правок.
- «Отмена» — ссылка на `/app/fields?schema=<slug>`.
- Страница правки:
  - `requireMe()`; не владелец → только текст «Поля меняет владелец аккаунта.»;
  - `GET /v1/schemas/{slug}?raw=true` (404 → `notFound()`), `GET …/labels` → `labels` (непустые `label` по
    `key`), `GET /v1/dictionaries`;
  - заголовок «Поля: {title}», под ним «Сохранение создаёт версию {version+1}. Обработанные документы остаются на
    своей версии.».

- [ ] **Step 1:** `ApiError.errors` + `saveSchema`.
- [ ] **Step 2:** `SchemaFieldCard`, `SchemaEditor`, страница правки.
- [ ] **Step 3:** `npm test`, `npm run lint`, `npm run build` → зелёные.
- [ ] **Step 4:** ручная проверка (`npm run dev` + локальный API из `web/README.md`):
  - `/app/fields/invoice_ru/edit`, добавить «Номер договора» → под названием `nomer_dogovora`, сводка
    «добавлено 1 поле»;
  - сохранить → редирект, версия 2;
  - поле `enum` без вариантов → ошибка у поля, POST не ушёл (вкладка Network);
  - удалить `vat` → сообщение про правило.
- [ ] **Step 5:** коммит `feat(web): редактор полей типа документа`.

### Task 5: Правила документа — список, удаление, три добавления

**Files:**
- Create: `web/src/components/app/DocRulesList.tsx`
- Modify: `web/src/components/app/SchemaEditor.tsx` (подключение, ошибки `rules.<j>`)

**Interfaces:**
- Consumes: `rulePhrase`, `sumRule`, `rowProductRule`, `accountBikRule` (задача 2), `titleResolver`,
  `toSchema` (задача 3).
- Produces: `DocRulesList` props `{ doc: EditorDoc; titleOf: (key: string) => string; errors: Record<string, string>;
  onChange(rules: RuleSpec[]): void }`.

Поведение:
- Раздел «Правила проверки документа». Список `<ul>`: фраза `rulePhrase`, ошибка `rules.<j>` под ней
  (`role="alert"`), кнопка «Удалить правило» с `window.confirm`. Непонятые правила тоже показываются
  (`Правило {kind}`) и сохраняются как есть.
- Три добавления — `<details>` с формой из селектов и кнопкой «Добавить». Кандидаты берутся из
  `toSchema(doc)`:
  - «Сумма колонки = поле»: колонка — числовые (`number`/`integer`) колонки таблицы, поле — числовые поля
    верхнего уровня;
  - «Кол-во × цена = сумма в строке»: три числовые колонки, все разные;
  - «Счёт сходится с БИК»: два разных текстовых поля. По умолчанию БИК — поле с проверкой `bik`.
- Кандидатов нет → форма недоступна с причиной: «Нужна таблица с числовыми колонками и числовое поле» / «Нужны
  три числовые колонки таблицы» / «Нужны два текстовых поля».
- Точный дубль существующего правила не добавляется: «Такое правило уже есть».
- Новое правило ссылается на имена. Если имя нового поля потом сменится, `withNames` (задача 3) перепишет
  ссылку.

- [ ] **Step 1:** реализация.
- [ ] **Step 2:** `npm run lint`, `npm run build` → зелёные.
- [ ] **Step 3:** ручная проверка:
  - на счёте видны 4 фразы, у `total_consistent` — `«Всего к оплате» = «Итого без НДС» + «НДС»`;
  - удалить правило `vat` → поле `vat` удаляется;
  - добавить «Сумма колонки = поле» и сохранить → правило в `GET …?raw=true`.
- [ ] **Step 4:** коммит `feat(web): правила документа в редакторе полей`.

### Task 6: Новый тип, входы со страницы «Поля», откат к стандартному

**Files:**
- Create: `web/src/app/app/fields/new/page.tsx`, `web/src/components/app/ResetSchemaButton.tsx`
- Modify: `web/src/app/app/fields/page.tsx`, `web/src/app/app/fields/actions.ts` (`resetSchema`)

**Interfaces:**
- Consumes: `SchemaEditor` (задача 4), `GET …?raw=true&builtin=true` (задача 1).
- Produces: `resetSchema(slug: string, expectedVersion: number): Promise<{ error?: string }>` — server action.
  - GET встроенной (`raw=true&builtin=true`), затем POST её тела с `expected_version`.
  - Успех → redirect как у `saveSchema`. Ошибки — как у `saveSchema`.

Поведение:
- `/app/fields/new`:
  - не владелец → «Поля меняет владелец аккаунта.»;
  - иначе `SchemaEditor initial={null}`, `takenSlugs` — slug'и из `GET /v1/schemas` (сервер всё равно проверит
    через `expected_version=0`), справочники;
  - заголовок «Новый тип документа».
- `/app/fields`, только владельцу:
  - «Новый тип документа» (`Button variant="ghost" href="/app/fields/new"`) рядом с заголовком;
  - «Изменить поля» (`Button href=/app/fields/<slug>/edit`) у заголовка выбранного типа;
  - если `current.builtin === false` и `GET …?raw=true&builtin=true` = 200 → `ResetSchemaButton`.
    - `confirm`: «Вернуть стандартные поля? Новые документы пойдут по стандартной схеме, обработанные не
      изменятся. Названия удалённых полей сотрутся.»;
    - ошибка → `role="alert"`.
- `?saved=<N>` (только цифры) → `role="status"` над формой: «Сохранено: версия N. Новые документы пойдут по ней,
  обработанные остаются на своей версии.»

- [ ] **Step 1:** реализация.
- [ ] **Step 2:** `npm run lint`, `npm run build` → зелёные.
- [ ] **Step 3:** ручная проверка:
  - новый тип со slug `invoice_ru` → ошибка «уже есть» у служебного имени, без запроса;
  - после перекрытия счёта кнопка отката видна, откат даёт версию 3 со встроенным телом;
  - оператор кнопок не видит.
- [ ] **Step 4:** коммит `feat(web): новый тип документа и откат к стандартным полям`.

### Task 7: Живая проверка на демо

**Files:**
- Modify: `tests/e2e/fake_responses.json` — ключ `waybill_ru` с синтетикой:
  - `number "ПЛ-0007"`, `issued "2026-10-29"`, `voditel "Петров П. П."`, `avtomobil "ГАЗель А123ВС"`;
  - `poezdki`: две строки `{punkt_naznacheniya, probeg_km}` (`"Склад", 12` и `"Магазин", 30.5`);
  - `_uncertain []`.
- Найденные дефекты чинятся в файлах их задач отдельными коммитами `fix: …`.

- [ ] **Step 1:** стенд с fake-экстрактором:
  `docker compose -f docker-compose.yml -f docker-compose.demo.yml -f docker-compose.e2e.yml up -d --build`, затем
  `… run --rm worker docflow seed-demo --reset`. Логин и пароль из вывода не печатать и не сохранять.
- [ ] **Step 2:** в браузере (Playwright MCP) владельцем:
  - «Счёт» → «Изменить поля» → «Номер договора» (Текст) → сводка «добавлено 1 поле» → сохранить → «Сохранено:
    версия 2»;
  - старый документ счёта открывается, его карточка и PDF как раньше;
  - подписи компании (если заданы на «Полях») на месте.
- [ ] **Step 3:** «Новый тип документа» → «Путевой лист»:
  - slug вручную `waybill_ru`;
  - поля «Водитель», «Автомобиль» (Текст), «Поездки» (Таблица: «Пункт назначения» Текст, «Пробег, км» Число);
  - имена — ровно `voditel`, `avtomobil`, `poezdki`, `punkt_naznacheniya`, `probeg_km`;
  - сохранить → версия 1.
- [ ] **Step 4:** тип виден везде:
  - выбор типа в загрузке, условие правила маршрутизации, «Поля», форма реестра Excel, сопоставление в
    подключении;
  - загрузить синтетический PNG (`PIL`, белый лист в scratchpad) типа «Путевой лист» → в карточке водитель,
    автомобиль и таблица поездок.
- [ ] **Step 5:** `.venv/Scripts/python -m pytest -q`, `ruff check src tests`,
  `pytest -m integration tests/db` (compose Postgres 55432), в `web/` `npm test`, `npm run lint`,
  `npm run build` → зелёные. Коммит `test(e2e): заготовка ответа для путевого листа`. Итог — коротко в отчёт,
  без паролей и ключей.

## Self-review

- Покрытие спеки:
  - редактор, адреса, шапка, slug — задачи 4 и 6;
  - поля, типы, проверки, варианты, таблица — задачи 3 и 4;
  - имя транслитом и неизменность имени — задачи 2 и 3;
  - `number`/`issued` по умолчанию с предупреждением — задачи 3 и 4;
  - запрет удаления под правилом — задачи 3, 4 и 5;
  - правила списком, удаление, три добавления — задачи 2 и 5;
  - новая версия, ошибки у поля, сводка — задачи 1, 2 и 4;
  - подписи остаются, удалённые чистятся — задача 1;
  - откат — задачи 1 и 6;
  - «работает везде» и живая проверка — задача 7;
  - сверка версии — задача 1;
  - формат API — только добавления (Global Constraints).
- Типы сходятся: `EditorDoc` / `EditorField` / `EditorColumn`, `RuleSpec`, `ValidationItem`, ключи ошибок
  `fields.<i>[.columns.<j>]` / `rules.<j>` / `general` одинаковы в `errorTargets`, `validateDoc` и
  `SchemaEditor`.
- Review Focus 1–5 закреплены тестами в задачах 1–3.
