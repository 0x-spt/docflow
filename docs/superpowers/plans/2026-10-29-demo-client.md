# Демо-клиент: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** команда `docflow seed-demo [--reset]` поднимает чистый демо-тенант «Северный Путь»: синтетические сканы
проходят настоящий конвейер, результат — реалистичная смесь статусов, выгрузки и уведомления реально срабатывают.

**Architecture:** пакет `src/docflow/demo/` из четырёх модулей:
- `requisites.py` — валидные вымышленные реквизиты;
- `data.py` — сценарии документов, демо-экстрактор, своя схема, справочник, правила;
- `render.py` — синтетическое фото документа;
- `seed.py` — сброс и создание тенанта, прогон `process_document`, проверки оператором, сдвиг времени.

CLI вызывает `seed.py`. Mailpit в `docker-compose.demo.yml` принимает почтовые уведомления.

**Tech Stack:** Python 3.12, SQLAlchemy async, Pillow, numpy, arq (ArqEnqueuer), pytest на SQLite.

**Spec:** `docs/superpowers/specs/2026-10-29-demo-client-design.md`

## Global Constraints

- Имя тенанта ровно `Демо — ООО «Северный Путь»` (константа `DEMO_TENANT_NAME`). По нему и только по нему
  работает `--reset`.
- Никаких реальных реквизитов. ИНН/КПП/БИК/счета генерируются и проходят `valid_inn`/`valid_kpp`/`valid_bik`/
  `valid_account` из `docflow.validation.checksums`. Названия компаний и людей вымышленные. Реальные ИНН из
  тестов (`7707083893`) и реальный БИК `044525225` в демо не использовать.
- Статусы в БД ставит только продукт:
  - `process_document`;
  - `review_document` + `route_approved`.
  Строки `Export` руками не создаются и не меняются.
- `DemoExtractor` импортируется только из `docflow.demo`. `worker.py` и `api/` его не знают.
- Детерминизм: `random.Random(20261029)` и фиксированная опорная дата `today`, которая передаётся параметром
  (по умолчанию `date.today()` по Москве).
- Тенант: `extractor="ollama"`, `auto_ok=True`.
- Комментарии и сообщения по-русски, стиль как в окружающем коде. Коммиты без Co-Authored-By и без
  «Generated with Claude Code».

## Review Focus

1. **`--reset` при чужих тенантах с похожим именем.** Если тенант называется «Демо — ООО «Северный Путь» (копия)»,
   он не удаляется. Сравнение точное.
   → тест в задаче 4: `test_reset_keeps_other_tenants`.
2. **Повторный запуск без `--reset`, когда демо уже есть.** Команда отказывается с подсказкой про `--reset` и
   ничего не создаёт. → тест в задаче 4: `test_seed_twice_refuses`.
3. **Нет `DOCFLOW_SECRETS_KEY`, а переданы `--google-key`/`--bitrix-webhook`/`--telegram-*`.** Понятная ошибка
   до создания тенанта, без полусозданного демо. Почте без пароля ключ не нужен.
   → тест в задаче 4: `test_secret_flags_without_key_fail_before_create`.
4. **Опорная дата у конца месяца или в понедельник.** Все `issued` не позже `today` (правило
   `date_not_future`), и все документы попадают в 21 день назад.
   → тест в задаче 2: `test_scenarios_dates_in_window` параметризован по датам `2026-03-01`, `2026-12-31`,
   `2026-11-02`.
5. **Шрифт DejaVu не найден** (локальный Windows без шрифта). `render` падает с понятным сообщением, где взять
   шрифт, а не с трейсбеком PIL. → тест в задаче 3: `test_render_without_font_message`.

---

### Task 1: Генератор вымышленных реквизитов

**Files:**
- Create: `src/docflow/demo/__init__.py` (пустой докстринг пакета)
- Create: `src/docflow/demo/requisites.py`
- Test: `tests/demo/test_requisites.py`

**Interfaces:**
- Produces:
  - `make_inn(rng: random.Random, *, person: bool = False) -> str` — 10 цифр для юрлица, 12 для ИП. Регион
    берётся из набора `("77", "78", "50", "47", "66")`.
  - `make_kpp(inn: str, rng) -> str` — 9 цифр; первые 4 совпадают с началом ИНН, затем `"01001"`.
  - `DEMO_BANK = Bank(name='АО «Банк Северный Демо»', bik="044599731", corr="30101810...")`. БИК вымышленный:
    реальным код `…731` в 0445 быть не обязан. Корр. счёт `301018100000000000` + 3 последние цифры БИК —
    стандартная форма.
  - `make_account(bik: str, rng) -> str` — 20 цифр, начинается с `40702810`, контрольная цифра на позиции 9
    (индекс 8) подобрана под `valid_account(account, bik)`.

- [ ] **Step 1: тесты.**
  - `test_inn_valid`: 200 штук `make_inn` для юрлиц и 200 для ИП → все `valid_inn`, длины 10 и 12.
  - `test_kpp_valid`: `valid_kpp(make_kpp(inn, rng))`.
  - `test_account_matches_bik`: 200 счетов → `valid_account(acc, DEMO_BANK.bik)`, `valid_bik(DEMO_BANK.bik)`.
  - `test_deterministic`: два `Random(1)` дают одинаковые последовательности.
  - `test_not_known_real`: ни один из 500 ИНН не равен `"7707083893"`.
- [ ] **Step 2:** `.venv/Scripts/python -m pytest tests/demo/test_requisites.py -q` → FAIL (модуля нет).
- [ ] **Step 3:** реализовать. Контрольные разряды ИНН — веса как в `checksums._inn_digit`, импортировать
  оттуда, не копировать. Счёт: перебрать цифру 0–9 на индексе 8, пока не пройдёт `valid_account`.
- [ ] **Step 4:** тесты → PASS.
- [ ] **Step 5:** коммит `feat(demo): вымышленные реквизиты с верными контрольными суммами`.

### Task 2: Сценарии демо-документов

**Files:**
- Create: `src/docflow/demo/data.py`
- Test: `tests/demo/test_data.py`

**Interfaces:**
- Consumes: задача 1.
- Produces:
  - `DEMO_TENANT_NAME = "Демо — ООО «Северный Путь»"`.
  - `BUYER` — dataclass `Company(name, inn, kpp, address)` демо-покупателя.
  - `ACT_SCHEMA: dict` — тело своей схемы тенанта: slug `act_works`, version 1, title «Акт выполненных работ».
    - Поля:
      - `number`, обязательное;
      - `issued` (date, обязательное, `date_not_future`);
      - `contractor` (string, правило `{kind: dictionary, params: {name: suppliers}}`);
      - `contractor_inn` (`inn`);
      - `customer`;
      - `items` — table: name, qty, unit, price, amount; обязательное;
      - `total` (number, обязательное).
    - Правила: `table_sum_equals(items.amount → total)`, `table_row_product`.
    - Схема проходит `DocSchema.model_validate`.
  - `SUPPLIERS_DICT: dict` — `{"title": "Поставщики", "entries": [{"value": ..., "aliases": [...]}]}`: 8
    вымышленных поставщиков.
  - `@dataclass Scenario`:
    - `key: str` — латиница, уникальный;
    - `schema: str` — `invoice_ru` | `upd_ru` | `act_works`;
    - `answer: dict | None` — ответ модели; `None` значит ошибку извлечения;
    - `days_ago: int` (0–20); `hour: int` (9–18);
    - `expect: Literal["ok", "needs_review", "failed"]`;
    - `review: Literal["approve", "approve_edit", "reject"] | None`; `review_note: str | None`;
    - `fix: dict | None` — правки оператора для `approve_edit`;
    - `pages: int = 1`.
  - `scenarios(today: date) -> list[Scenario]` — ровно 24:
    - 17 `ok`;
    - 6 `needs_review`, у трёх из них `review`: approve_edit (сумма строк поправлена), approve (согласование
      суммы выше 500 000 ₽), reject (note «Дубль счёта № …»);
    - 1 с `answer=None` (`expect="failed"`, key `blurry_photo`).
  - Причины трёх оставшихся `needs_review`:
    - сумма строк ≠ «Итого»;
    - согласование > 500 000 ₽;
    - контрагента акта нет в справочнике.
  - Состав по схемам: 10 счетов, 8 УПД, 6 актов.
  - `issued` = дата создания минус 0–3 дня, но не позже `today`.
  - `class DemoExtractor` — `model = "demo"`; `__init__(self, answer: dict | None)`;
    `extract(schema, images) -> Extraction` возвращает `dict(answer)`, при `None` бросает
    `ExtractionError("фото слишком размыто, текст не читается")`. `ExtractionError` берётся из
    `docflow.extraction.base`.
  - `RULES_TEMPLATE(dest: DemoDestinations) -> list[dict]` — правила в формате `RulesIn`.
    `DemoDestinations` — dataclass с id: `mail_accounting`, `mail_operator` (обязательные); `sheets`, `bitrix`,
    `telegram` (`uuid | None`).
    - «Счета и УПД → учёт»: schema `invoice_ru`, затем отдельное правило для `upd_ru`; `export_to`
      `[sheets, bitrix]` без `None`; `notify` — `[mail_accounting]`.
    - «Крупная сумма — согласование»: `total gt 500000`, `require_approval`, по `invoice_ru` и `upd_ru`.
      Нужны два правила, потому что schema одна на правило.
    - «Акты ключевых подрядчиков»: `act_works`, `contractor in_dictionary suppliers`, `export_to [sheets]`.
    - «На проверку — оператору»: без схемы, `notify [mail_operator, telegram]`.
  - Правила проходят `RulesIn.model_validate({"rules": ...})`.

- [ ] **Step 1: тесты** (без БД, чистые функции продукта).
  - `test_counts`:
    - 24 сценария, ключи уникальны;
    - по `expect`: ok 17, needs_review 6, failed 1;
    - по схемам: 10/8/6;
    - `review`: ровно по одному approve, approve_edit, reject.
  - `test_expected_status` (параметризован по каждому сценарию, кроме failed) — тот же путь, что в
    `pipeline._run`:
    - `normalize(schema, answer)`;
    - `resolve_dictionaries(..., {"suppliers": entries})`;
    - `evaluate(schema, data, today=today)`;
    - `evaluate_rules(rules, schema, data, {"suppliers": entries})`;
    - если `decide_status(...) == "ok"`, добавить `approval_issues(decision)`;
    - сверить `decide_status(issues) == scenario.expect`.
    Правила берутся из `RULES_TEMPLATE` с фиктивными uuid. Схемы: `builtin_schemas()` и
    `DocSchema.model_validate(ACT_SCHEMA)`. Записи справочника: `DictEntry(value, tuple(aliases))` — свериться с
    конструктором в `validation/dictionaries.py`.
  - `test_approve_edit_fix_is_ok`: данные + `fix` после normalize/evaluate дают `ok`, если не считать
    `approval_required`.
  - `test_scenarios_dates_in_window[2026-03-01|2026-12-31|2026-11-02]`: все `issued` ≤ today, все
    `0 ≤ days_ago ≤ 20`.
  - `test_requisites_valid`: у всех ответов ИНН/КПП/БИК/счёт валидны, кроме сценариев, где ошибка в них и есть
    причина проверки. Таких в этом плане нет: причины — суммы, согласование, справочник.
  - `test_rules_valid`: `RulesIn.model_validate` для шаблона с `sheets=bitrix=telegram=None` и со всеми id.
  - `test_act_schema_valid`.
  - `test_deterministic`: `scenarios(d) == scenarios(d)`.
- [ ] **Step 2:** прогон → FAIL.
- [ ] **Step 3:** реализовать `data.py`.
  - Позиции реалистичные для стройматериалов и услуг: цемент М500, профлист, доставка, монтаж и т. п.
  - Количества и цены округлены; `amount = qty*price`.
  - НДС 20 % сверху у счетов: `subtotal`, `vat`, `total`.
  - УПД — по полям `upd_ru.yaml`, включая `status` (1 или 2 — смотреть enum в yaml), `vat_amount` и
    `amount_with_vat` по строкам.
  - Покупатель во всех документах — `BUYER`.
- [ ] **Step 4:** прогон → PASS. Плюс `.venv/Scripts/ruff check src/docflow/demo tests/demo`.
- [ ] **Step 5:** коммит `feat(demo): сценарии демо-документов и правила`.

### Task 3: Синтетическое фото документа

**Files:**
- Create: `src/docflow/demo/render.py`
- Test: `tests/demo/test_render.py`

**Interfaces:**
- Consumes: `Scenario`, `BUYER`, схемы — из задачи 2.
- Produces:
  - `class FontMissing(RuntimeError)`;
  - `find_font() -> Path` — `Settings().pdf_font_path`, затем `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`,
    затем `C:/Windows/Fonts/DejaVuSans.ttf`. Bold — `DejaVuSans-Bold.ttf` рядом, при отсутствии обычный.
    Ничего не найдено → `FontMissing("нет шрифта DejaVu: установите fonts-dejavu-core или задайте
    DOCFLOW_PDF_FONT_PATH")`;
  - `render_photo(scenario: Scenario, schema_title: str, *, seed: int) -> bytes` — JPEG.

- [ ] **Step 1: тесты.**
  - `test_render_is_jpeg_and_deterministic`: два вызова с одним seed дают одинаковые байты, начало `b"\xff\xd8"`.
  - `test_sheet_found`: `find_sheet(Image.open(BytesIO(jpeg)).convert("RGB"))` не `None`. Четыре угла;
    площадь листа больше 40 % кадра.
  - `test_render_without_font_message`: при monkeypatch `find_font` → `FontMissing` текст исключения содержит
    `fonts-dejavu-core`.
  - Тесты пропускаются (`pytest.skip`), если шрифта нет, как фикстура `digital_font` в `tests/conftest.py`.
- [ ] **Step 2:** прогон → FAIL.
- [ ] **Step 3:** реализовать.
  - Лист A4 1240×1754 на белом:
    - шапка (поставщик, ИНН/КПП; для счёта — блок банка);
    - заголовок «<Тип> № N от DD.MM.YYYY»;
    - покупатель;
    - таблица с сеткой;
    - итоги, «Сумма прописью» можно опустить;
    - синяя круглая печать с названием поставщика по окружности или двумя строками внутри;
    - подпись — ломаная кривая.
  - Затем:
    - лист повёрнут на ±3° с лёгкой перспективой (`Image.transform` с `PERSPECTIVE`);
    - лежит на тёмно-сером «столе» 1600×2100 с шумом;
    - мягкая тень;
    - гауссов шум; JPEG quality 82.
  - Для `blurry_photo` — сильный `GaussianBlur(6)`.
  - Числа печатаются по-русски: `12 500,00`.
- [ ] **Step 4:** прогон → PASS, ruff.
- [ ] **Step 5:** коммит `feat(demo): синтетическое фото документа`.

### Task 4: Создание и сброс демо-тенанта, команда CLI

**Files:**
- Create: `src/docflow/demo/seed.py`
- Modify: `src/docflow/storage/blobs.py` — `delete(key)` в `BlobStore`, `LocalBlobStore`, `S3BlobStore`.
  Отсутствующий ключ — не ошибка.
- Modify: `src/docflow/cli.py` — подкоманда `seed-demo`.
- Test: `tests/demo/test_seed.py`, `tests/storage/` (тест `delete` для LocalBlobStore рядом с существующими).

**Interfaces:**
- Consumes: задачи 1–3; `process_document`, `Deps`, `SchemaResolver` (`docflow.pipeline`); `review_document`,
  `route_approved` (`docflow.review`); `encrypt_secrets`; `REGISTRY`.
- Produces:
  - `class DemoExists(Exception)`; `class DemoError(Exception)` (сообщение для оператора).
  - `@dataclass SeedOptions`:
    - `reset: bool = False`;
    - `today: date | None = None`;
    - `smtp_host: str = "mailpit"`; `smtp_port: int = 1025`;
    - `google_key: dict | None = None`; `google_sheet: str | None = None`;
    - `bitrix_webhook: str | None = None`;
    - `telegram_token: str | None = None`; `telegram_chat: str | None = None`;
    - `owner_email: str = "demo@severnyput.example"`; `operator_email: str = "operator@severnyput.example"`.
  - `@dataclass SeedResult`: `tenant_id`, `owner_email`, `owner_password`, `operator_email`, `operator_password`,
    `api_key`, `counts: dict[str, int]` (по статусам).
  - `async def reset_demo(sm, blobs) -> bool` — удаляет тенант с именем ровно `DEMO_TENANT_NAME`:
    - сначала blob-ключи всех его документов: `files`, `scan_key`, `page_key` по числу страниц;
    - затем строки: документы (выгрузки и доставки уходят каскадом), остальное каскадом от тенанта.
    Возвращает, был ли тенант.
  - `async def seed_demo(sm, blobs, ocr, enqueuer, settings, opts: SeedOptions) -> SeedResult`:
    1. проверка флагов секретов против `settings.secrets_key` (Review Focus 3);
    2. демо есть и не `reset` → `DemoExists`;
    3. `reset` → `reset_demo`;
    4. тенант + ApiKey + два пользователя: пароли `secrets.token_urlsafe(12)`, `hash_password`;
    5. `SchemaRecord(tenant_id, act_works, 1, ACT_SCHEMA)`; `Dictionary(suppliers)`;
    6. подключения:
       - два `email` («Почта бухгалтерии», «Почта оператора»): config host/port/`security: "none"`,
         from `docflow@severnyput.example`, `to_addrs` — `buh@…` / `operator@…`;
       - Google/Bitrix/Telegram — по флагам: секреты через `encrypt_secrets`, `mapping={}` (маппинг по
         умолчанию из коннектора).
       Имена полей config/secrets — сверять с `config_fields`/`secret_fields` коннектора, а проверять через
       ту же функцию, что `api/destinations.py` (`_check_config`, `_check_secrets`). Если они приватные —
       вынести в общий модуль без изменения поведения API;
    7. `RoutingRules` из `RULES_TEMPLATE`;
    8. по каждому сценарию в порядке от старых к новым:
       - `created = now_msk − days_ago` в `hour:mm`;
       - `Document(status="queued", files=[original_key(...)], mode="gray", created_at=created, ...)`;
       - `blobs.put` фото;
       - `process_document(Deps(..., extractors={"ollama": DemoExtractor(answer)}), id)`;
       - `review`, если задано: `review_document(... principal=оператор ...)` и при одобрении `route_approved`;
       - сдвиг `claimed_at`/`finished_at` на `created + 1–3 мин`, `reviewed_at` на `created + 2–6 ч`.
    Статус после конвейера сверяется с `scenario.expect`, расхождение даёт `DemoError` с ключом сценария.
  - CLI:
    - `docflow seed-demo [--reset] [--smtp-host] [--smtp-port] [--google-key PATH] [--google-sheet ID]
      [--bitrix-webhook URL] [--telegram-token] [--telegram-chat]`;
    - внутри: настоящий `S3/Local` из настроек, `OcrMyPdf()`, `ArqEnqueuer(await create_pool(...))` — как в
      `worker.startup`; `DemoExists` → `UserError` с подсказкой `--reset`;
    - печать:
      - адрес кабинета `settings.public_site_url + "/login"`;
      - оба логина с паролями и «пароль больше не будет показан»;
      - API-ключ;
      - счётчики статусов;
      - строка «Это демо: данные синтетические, модель — заготовки (model=demo)»;
      - адрес Mailpit `http://localhost:8025`, если smtp_host == "mailpit".

- [ ] **Step 1: тесты** на `sm`-фикстуре (SQLite), `LocalBlobStore(tmp_path)`, `NoOcr` из `docflow.pdf.build`,
  `ListEnqueuer`, настройки с `secrets_key` (Fernet-ключ генерировать в тесте). Опорная дата фиксирована.
  - `test_seed_creates_expected_mix`:
    - counts `{"ok": 19, "needs_review": 3, "failed": 2}`: ok — 17 + 2 одобрены; failed — отклонён + размытое;
    - есть 2 пользователя, схема `act_works`, справочник, 2 подключения, правила;
    - `Export` созданы только через продукт: у `ok`-документов есть `notify_new` на почту бухгалтерии, у
      `needs_review` — `notify_review` на почту оператора;
    - `ListEnqueuer.calls` содержит `deliver_export`.
  - `test_dates_backdated`:
    - `created_at` у всех документов в пределах `[today−21д, today]`;
    - `finished_at ≥ created_at` и `finished_at − created_at < 1 ч`;
    - у проверенных `reviewed_at > finished_at`.
  - `test_seed_twice_refuses` → второй вызов без reset бросает `DemoExists`, документов столько же.
  - `test_reset_recreates`: reset → новый `tenant_id`, старых документов и blob-файлов нет (проверка
    `blobs.exists`).
  - `test_reset_keeps_other_tenants`: тенанты «Ромашка» и `DEMO_TENANT_NAME + " (копия)"` с документом —
    после reset на месте с документами.
  - `test_secret_flags_without_key_fail_before_create`: `secrets_key=""`, `bitrix_webhook` задан → `DemoError`,
    тенантов 0.
  - `test_optional_destinations`: с `bitrix_webhook` и telegram (секрет-ключ есть) → 4 подключения, правила
    ссылаются на bitrix и telegram, секреты расшифровываются обратно.
  - `test_cli_seed_demo_parses` (в `tests/test_cli.py` по образцу соседних): парсер принимает флаги.
  - `LocalBlobStore.delete`: удаляет, на отсутствующем ключе молчит.
- [ ] **Step 2:** прогон → FAIL.
- [ ] **Step 3:** реализовать. `S3BlobStore.delete` — `delete_object` тем же клиентом, что `put`.
- [ ] **Step 4:** весь набор: `.venv/Scripts/python -m pytest -q -m "not e2e"` → всё зелёное (было 878),
  `ruff check src tests`.
- [ ] **Step 5:** коммит `feat(demo): команда seed-demo — демо-тенант за одну команду`.

### Task 5: Стек для демо и инструкция

**Files:**
- Create: `docker-compose.demo.yml`
- Modify: `docker/Dockerfile` — `fonts-dejavu-core` в цель `worker` (фото рисуются в воркере).
- Modify: `README.md` — раздел «Демо для показа клиенту».

**Interfaces:**
- Consumes: команда из задачи 4.

- [ ] **Step 1:** `docker-compose.demo.yml`:
  - сервис `mailpit` (`axllent/mailpit:v1.27`, порт `127.0.0.1:8025:8025`, SMTP 1025 только внутри сети);
  - `DOCFLOW_WEBHOOK_ALLOW_PRIVATE: "true"` для `api` и `worker`;
  - комментарий-шапка с командами, как у `docker-compose.e2e.yml`.
- [ ] **Step 2:** README — короткий раздел:
  - поднять стек с оверлеем;
  - `docker compose -f docker-compose.yml -f docker-compose.demo.yml run --rm worker docflow seed-demo`;
  - где кабинет и письма;
  - `--reset`;
  - флаги внешних систем со ссылкой на `docs/connectors.md`;
  - предупреждение: оверлей разрешает частные адреса, в проде не использовать.
- [ ] **Step 3: живая проверка.**
  - Сборка: `docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build`.
  - Запуск `seed-demo`, вывод без ошибок.
  - В Mailpit не меньше 20 писем.
  - В кабинете под демо-логином 24 документа.
  - Хотя бы один цифровой PDF и один скан открываются.
- [ ] **Step 4:** коммит `feat(demo): стек с Mailpit и инструкция для показа`.
