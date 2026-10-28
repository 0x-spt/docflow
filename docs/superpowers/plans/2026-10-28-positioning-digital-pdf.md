# Позиционирование сайта и цифровой PDF — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Цифровая версия документа в PDF (API + кабинет), поле «куда выгружать» в заявке, лендинг с новым
позиционированием (автовыгрузка в 1С/CRM/таблицы) и адаптивностью от 320 px.

**Architecture:** Бэкенд рендерит PDF из данных документа и схемы через fpdf2 (DejaVu Sans). Сайт — те же
Next.js-компоненты лендинга, переписанные под новую логику; кабинет получает ссылку на цифровой PDF через
существующий прокси-обработчик файлов.

**Tech Stack:** FastAPI, fpdf2, pydantic; Next.js 15, Tailwind v4, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-28-positioning-digital-pdf-design.md` (и исходная
`2026-10-28-website-mvp-design.md` — всё, что новая не меняет).

## Global Constraints

- Ветка `website-mvp` (продолжение). Коммиты без Co-Authored-By. Кэши на E:. Не трогать PostgreSQL на :5432.
- Весь текст — по-русски. Дизайн-токены и правила доступности — как в плане `2026-10-28-website-mvp.md`
  (Global Constraints), без изменений.
- Анти-цели в силе: никаких выдуманных клиентов, отзывов, логотипов клиентов, цифр экономии, цен. Логотипы
  сторонних систем не используются — только названия текстом.
- Проверка сайта: lint, tsc, build, Playwright e2e (`PLAYWRIGHT_BROWSERS_PATH=E:\dev\ms-playwright`).

## Review Focus

1. Документ без части полей (null) или с пустой таблицей → PDF рисуется с «—», не падает.
2. Очень длинные значения (название 200 символов, 100 строк таблицы) → перенос строк и страниц, без обрезки.
3. Документ в `failed`/`processing` → 409 на цифровой PDF; кнопка в кабинете не показывается.
4. Ширина 320 px → без горизонтальной прокрутки на всех публичных страницах.
5. Старый тенант без `targets` в заявке и API-клиенты, не присылающие `targets` → заявка принимается.

---

### Task 1: Цифровой PDF и «куда выгружать» в заявке (бэкенд)

**Files:** Modify `src/docflow/schemas/model.py` (`title: str | None = None` у `ColumnSpec`), встроенные схемы
`src/docflow/schemas/builtin/*.yaml` (русские `title` всем полям и колонкам), `src/docflow/api/documents.py`,
`src/docflow/serializers.py`, `src/docflow/api/leads.py`, `src/docflow/db/models.py`, `src/docflow/notify.py`,
`src/docflow/cli.py`, `src/docflow/config.py`; Create `src/docflow/pdf/digital.py`,
`migrations/versions/0006_lead_targets.py`; Test `tests/pdf/test_digital.py`, `tests/api/test_documents.py`,
`tests/api/test_leads_api.py`, `tests/schemas/*`.

**Interfaces:**
- Produces: `render_digital_pdf(schema: DocSchema, data: dict, *, status: str, created_at: datetime) -> bytes`
  в `docflow.pdf.digital`; `format_value(spec: ColumnSpec, value) -> str` (числа «12 500,50», целые без
  дробной части, даты ДД.ММ.ГГГГ, bool «да/нет», None/пусто «—»); подпись поля = `title or name`.
- Produces (настройка): `pdf_font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"`
  (+ Bold рядом); тесты ищут шрифт по этому пути, затем в `C:/Windows/Fonts/DejaVuSans.ttf`/`arial.ttf`,
  иначе `pytest.skip`.
- Produces (API): `GET /v1/documents/{id}/digital.pdf` → `application/pdf`,
  `Content-Disposition: inline; filename="<slug>-<number или id8>.pdf"`; 404 чужой/нет; 409
  «цифровая версия доступна для документов в статусах ok и needs_review».
- Produces (`document_payload`): `digital_pdf_url` — URL при статусе ok/needs_review, иначе null.
- Produces (лиды): поле `targets: list[Literal["1c","bitrix24","amocrm","google_sheets","excel","own_system","unknown"]]`
  (необязательное, до 7, без повторов), колонка `leads.targets` JSON (миграция 0006, существующим — `[]`),
  в Telegram и `docflow leads` — по-русски.

- [ ] Step 1: тесты (RED): `test_format_value_*` (число, целое, дата, bool, None), `test_render_digital_pdf_contains_text`
  (pypdfium2 извлекает текст: заголовок схемы, подпись поля, значение «12 500,50», строка таблицы, служебная строка
  со статусом), `test_render_digital_pdf_handles_nulls_and_long_values` (null-поля, 120 строк таблицы → >1 страницы,
  без исключений), `test_digital_pdf_endpoint_ok`, `_conflict_for_failed`, `_foreign_404`, `test_payload_digital_url`,
  `test_lead_targets_saved_and_optional`, `test_lead_targets_invalid_422`, `test_builtin_schemas_have_titles`.
- [ ] Step 2: реализация; миграция 0006; `title` в YAML встроенных схем.
- [ ] Step 3: `uv run pytest -m "not integration and not e2e" -q`, `uv run ruff check`, `tests/db` — зелёные;
  на стеке (`docker compose up -d --build api worker`) скачать digital.pdf документа со статусом ok и приложить
  в отчёт извлечённый текст первой страницы.
- [ ] Step 4: commit `feat: цифровая версия документа в PDF и «куда выгружать» в заявке`.

### Task 2: Лендинг: новое позиционирование, форма, адаптивность 320 px

**Files:** `web/src/app/page.tsx`, `web/src/components/landing/*`, `web/src/components/AuditForm.tsx`,
`web/src/app/audit/actions.ts`, `web/src/lib/types.ts`, `web/src/app/layout.tsx` (метаданные), `web/e2e/site.spec.ts`.

**Interfaces:** Consumes `POST /v1/leads` с `targets` (Task 1).

- [ ] Step 1: скилл `frontend-design:frontend-design`; блоки 1–13 из новой спецификации, тексты продающие и
  конкретные, без выдуманных фактов; схема «документ → DocFlow → системы» — на HTML/CSS/inline SVG.
- [ ] Step 2: форма — группа чекбоксов «Куда выгружать данные» (fieldset/legend), отправляется в `targets`.
- [ ] Step 3: шапка и все блоки без горизонтальной прокрутки на 320–1920; метаданные страницы (title,
  description, Open Graph) под новое позиционирование.
- [ ] Step 4: e2e: тест лендинга проверяет новые якоря/ключевые блоки; тест формы отправляет `targets` и находит
  их в `docflow leads`; новый тест `landing has no horizontal scroll at 320/768/1440`.
- [ ] Step 5: lint, tsc, build, e2e зелёные; commit `feat(web): лендинг про автовыгрузку в 1С, CRM и таблицы`.

### Task 3: Кабинет: цифровая версия PDF

**Files:** `web/src/app/app/documents/[id]/page.tsx`, `web/src/app/app/files/[id]/digital/route.ts`,
`web/src/lib/types.ts`, `web/e2e/site.spec.ts`.

- [ ] Step 1: route handler по образцу `files/[id]/pdf` (isDocId, 401 → /auth/expired, 404/409 passthrough,
  стрим, `private, no-store`); кнопка «Цифровая версия (PDF)» только при `digital_pdf_url`.
- [ ] Step 2: e2e: в тесте проверки документа ссылка есть и отдаёт `application/pdf`.
- [ ] Step 3: lint, tsc, build, e2e; commit `feat(web): цифровая версия документа в кабинете`.
