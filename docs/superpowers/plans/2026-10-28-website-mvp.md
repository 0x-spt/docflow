# Сайт DocFlow (MVP) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Публичный сайт (лендинг, заявка на аудит, политика), вход по email/паролю и кабинет (очередь, проверка,
загрузка, справочники, интеграции) поверх существующего API DocFlow, запуск в docker-compose.

**Architecture:** Бэкенд FastAPI получает пользователей и сессии, заявки, список/статистику документов, превью
страниц, ручную проверку и ключи API. Сайт — отдельное приложение Next.js в `web/`, которое ходит в API только
со стороны сервера: токен сессии живёт в httpOnly-cookie и никогда не попадает в JavaScript браузера.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, argon2-cffi; Next.js 15 (App Router),
React 19, TypeScript strict, Tailwind CSS v4, next/font (Inter Tight, JetBrains Mono), Playwright.

**Spec:** `docs/superpowers/specs/2026-10-28-website-mvp-design.md`

## Global Constraints

- Репозиторий `E:\CLAUDE PROJECT\docflow`, ветка `website-mvp` от `phase1-core`. Коммиты без Co-Authored-By
  и без «Generated with Claude Code». Кэши и тулчейны — на диске E: (`UV_CACHE_DIR=E:/dev/uv-cache`,
  npm cache уже на E:).
- Весь текст интерфейса и сообщений API — на русском. Комментарии в коде — по-русски, как в существующем коде.
- Бэкенд: стиль существующего кода (ruff, длина строки 120, тесты pytest на SQLite, `asyncio_mode=auto`).
  Команды: `uv run pytest -m "not integration and not e2e" -q`, `uv run ruff check`.
- Никогда не трогать нативный PostgreSQL на 127.0.0.1:5432 (у стека — 55432).
- Токены: ключ API `dfk_…` (машины), сессия `dfs_…` (люди). В БД — только sha256 токена.
- Пароли — argon2id (`argon2-cffi`, параметры по умолчанию `PasswordHasher()`), минимум 10 символов.
- Сайт: браузер не получает токен; cookie `df_session`: httpOnly, SameSite=Lax, Path=/, Secure, если
  `NODE_ENV=production` и `COOKIE_SECURE` ≠ `false`; срок = `expires_at` сессии.
- Сайт → API: базовый адрес из `DOCFLOW_API_URL` (в compose `http://api:8000`), заголовки
  `Authorization: Bearer <dfs_…>` (если есть сессия), `X-Internal-Token: <DOCFLOW_INTERNAL_TOKEN>`,
  `X-Client-IP: <IP клиента из x-forwarded-for/x-real-ip>`.
- API доверяет `X-Client-IP` только при совпадающем `X-Internal-Token` (настройка `internal_token`, пусто =
  не доверять); иначе IP = `request.client.host`.
- Дизайн-токены (точные значения): фон `#F3F2EE`, текст `#0A0A0A`, акцент `#E5231B`, акцент для мелкого текста
  `#B8170F`, второстепенный текст `#5B5B5B`, белые плашки `#FFFFFF`, рамка 2px `#0A0A0A`, скругления 0,
  тень `6px 6px 0 #0A0A0A` (на :active — сдвиг на 6px и без тени). Шрифты Inter Tight (заголовки/текст),
  JetBrains Mono (метки, номера `[01]`, цифры). Без градиентов, эмодзи и стоковых иллюстраций.
- Доступность: настоящие `<button>`/`<a>`/`<label>`, видимый фокус (outline 3px `#E5231B`), контраст ≥ 4.5:1,
  цели ≥ 44px, `prefers-reduced-motion` отключает анимации.
- На лендинге — никаких выдуманных цифр, отзывов, логотипов и цен. Реквизиты, контакты, срок ответа —
  плейсхолдеры в квадратных скобках: `[ОПЕРАТОР ПД]`, `[ИНН]`, `[EMAIL]`, `[ТЕЛЕФОН]`, `[СРОК ОТВЕТА]`.

## Review Focus

1. Протухшая/отозванная сессия на `/app/*` → редирект на `/login` (cookie стирается), не 500 и не пустая
   страница. Тест — Task 7 (Playwright) и Task 1 (`/v1/me` с отозванной сессией → 401).
2. Два оператора подтверждают один документ одновременно → один получает результат, второй — 409
   «документ уже проверен». Тест — Task 4 (`test_review_twice_conflict`).
3. Форма заявки без JavaScript и двойная отправка → server action работает обычной отправкой формы; honeypot
   отсекает ботов без ошибки. Тесты — Task 2 (honeypot), Task 7 (отправка формы в Playwright).
4. Правка оператора «12 500,50» или даты «24.10» → приводится так же, как ответ модели (тот же `normalize`).
   Тест — Task 4 (`test_review_approve_normalizes_edits`).
5. Документ, обработанный до этой версии, без превью страниц → кабинет показывает «превью недоступно —
   скачайте PDF», а не битую картинку. Тест — Task 3 (404 на превью) и Task 6 (обработка 404 в UI).

---

### Task 1: Пользователи, сессии и вход (бэкенд)

**Files:**
- Modify: `pyproject.toml` (зависимость `argon2-cffi>=23`), `src/docflow/db/models.py`, `src/docflow/api/deps.py`,
  `src/docflow/api/app.py`, `src/docflow/cli.py`, `src/docflow/config.py`
- Create: `migrations/versions/0003_users_sessions.py`, `src/docflow/auth.py`, `src/docflow/api/auth.py`,
  `src/docflow/api/ratelimit.py`
- Test: `tests/test_auth.py`, `tests/api/test_auth_api.py`, `tests/test_cli.py`

**Interfaces:**
- Produces (модели): `User(id, tenant_id→tenants CASCADE, email: str unique, password_hash: str, role: str
  CHECK in ('owner','operator') default 'owner', created_at, disabled_at|None)`;
  `Session(id, user_id→users CASCADE, token_hash: str unique, created_at, expires_at, revoked_at|None)`.
- Produces (`docflow.auth`): `hash_password(p: str) -> str`, `verify_password(hash: str, p: str) -> bool`,
  `generate_session_token() -> tuple[str, str]` (токен `dfs_` + `secrets.token_urlsafe(32)`, sha256),
  `SESSION_TTL = timedelta(days=30)`, `MIN_PASSWORD = 10`.
- Produces (`docflow.api.ratelimit`): `class SlidingWindowLimiter(limit: int, window_s: float)` с
  `hit(key: str) -> bool` (True = разрешено, учитывает попытку) и `reset(key)`; `client_ip(request, settings) -> str`.
- Produces (`docflow.api.deps`): `@dataclass Principal(tenant_id: UUID, user_id: UUID|None, role: str|None,
  session_id: UUID|None)`; `get_principal(request, session) -> Principal` принимает `dfk_` и `dfs_`
  (сессия: не отозвана, не истекла, пользователь не отключён); `PrincipalDep`; `get_tenant_id` остаётся и
  возвращает `principal.tenant_id`; `require_owner(principal) -> Principal` (403 «нужны права владельца»,
  если не сессия владельца).
- Produces (API): `POST /v1/auth/login {email, password}` → 200 `{token, expires_at, user: {id, email, role}}`;
  401 `{"detail": "неверный email или пароль"}`; 429 `{"detail": "слишком много попыток, попробуйте через 15 минут"}`.
  `POST /v1/auth/logout` → 204 (отзывает текущую сессию; для ключа API — 400).
  `GET /v1/me` → `{user: {id,email,role}|null, tenant: {id, name, extractor, auto_ok}}`.
- Produces (CLI): `docflow create-user <tenant_id> <email> [--role owner|operator] [--password P]` — без
  `--password` генерирует 16-символьный пароль и печатает его один раз.
- Produces (настройки): `internal_token: str = ""`.

- [ ] **Step 1:** Ветка: `git switch -c website-mvp` (от `phase1-core`). Добавить `argon2-cffi>=23`, `uv sync --all-extras`.
- [ ] **Step 2: Тесты** — в `tests/test_auth.py`: `test_hash_roundtrip` (verify верного → True, неверного → False,
  хеш начинается с `$argon2id$`), `test_session_token_shape` (префикс `dfs_`, хеш = sha256 токена).
  В `tests/api/test_auth_api.py` (фикстура пользователя создаёт `User` с `hash_password("correct-horse-1")`):
  `test_login_ok_and_me` (200, токен `dfs_`, `/v1/me` с ним → email и `tenant.name`),
  `test_login_wrong_password_and_unknown_email_same_401` (одинаковые тело и код),
  `test_login_email_case_insensitive`, `test_login_rate_limited` (6-я неудачная попытка на тот же email → 429,
  даже с верным паролем), `test_logout_revokes` (после logout `/v1/me` → 401),
  `test_expired_session_401`, `test_disabled_user_401`, `test_session_reaches_existing_endpoints`
  (`GET /v1/webhooks` с `dfs_` → 200), `test_api_key_me_has_null_user`,
  `test_client_ip_header_trusted_only_with_internal_token` (без токена `X-Client-IP` игнорируется).
  В `tests/test_cli.py`: `test_create_user_prints_password_once`, `test_create_user_duplicate_email_exit_1`,
  `test_create_user_unknown_tenant_exit_1`.
- [ ] **Step 3:** Прогнать — падают.
- [ ] **Step 4:** Реализовать модели, миграцию `0003` (таблицы `users`, `sessions`; имена ограничений по
  NAMING_CONVENTION, как в 0002), `docflow/auth.py`, лимитер (в памяти процесса; ключи `email:<lower>` 5/900 с и
  `ip:<ip>` 20/900 с; успешный вход сбрасывает счётчик email), роутер `docflow/api/auth.py`, `Principal` в deps,
  CLI. Email нормализуется `strip().lower()`; пароль < 10 символов в CLI → ошибка.
- [ ] **Step 5:** Тесты зелёные, весь набор и `ruff` зелёные, тест миграций (`tests/db`) зелёный.
- [ ] **Step 6:** Commit `feat: пользователи, сессии и вход в API`.

### Task 2: Заявки на аудит (бэкенд)

**Files:**
- Modify: `src/docflow/db/models.py`, `src/docflow/api/app.py`, `src/docflow/cli.py`, `src/docflow/config.py`, `.env.example`
- Create: `migrations/versions/0004_leads.py`, `src/docflow/api/leads.py`, `src/docflow/notify.py`
- Test: `tests/api/test_leads_api.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `SlidingWindowLimiter`, `client_ip` (Task 1).
- Produces (модель): `Lead(id, created_at, name, company|None, contact, niche|None, volume, documents|None,
  message|None, ip|None, notified: bool default False)`.
- Produces (API): `POST /v1/leads` (без авторизации) тело
  `{name: 1–100, company?: ≤200, contact: 3–200, niche?: ≤100, volume: "lt100"|"100-1000"|"1000-10000"|"gt10000"|"unknown",
  documents?: ≤2000, message?: ≤2000, consent: true, website?: str}` → 201 `{"id": "<uuid>"}`.
  `consent` не `true` → 422 с текстом «нужно согласие на обработку персональных данных». Непустой `website`
  (honeypot) → 201 с фиктивным id, в БД ничего. 6-я заявка за час с IP → 429.
- Produces (`docflow.notify`): `async notify_lead(lead: Lead, settings) -> bool` — POST в
  `https://api.telegram.org/bot<token>/sendMessage` (`chat_id`, текст заявки), таймаут 5 с, любые ошибки → False
  и запись в лог; без настроек — False без запроса.
- Produces (настройки): `leads_tg_token: str = ""`, `leads_tg_chat: str = ""`.
- Produces (CLI): `docflow leads [--limit 20]` — таблица последних заявок (дата, имя, компания, контакт, объём).

- [ ] **Step 1: Тесты** — `test_lead_created`, `test_lead_requires_consent` (422 и текст), `test_lead_validation`
  (параметризация: пустое имя, контакт из 2 символов, неизвестный volume, 2001 символ в message → 422),
  `test_honeypot_not_stored`, `test_lead_rate_limit_per_ip`, `test_lead_notifies_telegram` (respx: запрос ушёл,
  `notified=True`), `test_lead_telegram_failure_still_201` (respx 500 → 201, `notified=False`);
  CLI `test_leads_lists_recent`.
- [ ] **Step 2:** Прогнать — падают.
- [ ] **Step 3:** Реализовать модель, миграцию `0004`, роутер, `notify.py` (уведомление после commit,
  в `BackgroundTasks` FastAPI), CLI, настройки и строки в `.env.example`.
- [ ] **Step 4:** Тесты, весь набор, ruff — зелёные.
- [ ] **Step 5:** Commit `feat: заявки на аудит с уведомлением в Telegram`.

### Task 3: Список документов, статистика, превью страниц (бэкенд)

**Files:**
- Modify: `src/docflow/api/documents.py`, `src/docflow/pipeline.py`, `src/docflow/storage/blobs.py`
- Create: `src/docflow/api/stats.py`
- Test: `tests/api/test_documents.py`, `tests/api/test_stats_api.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `PrincipalDep`/`TenantDep` (Task 1).
- Produces: `page_key(document_id: str, n: int) -> str` = `f"pages/{document_id}/{n}.jpg"` (n с 1).
  Конвейер в `_render` кладёт `model_jpeg` каждой страницы по `page_key` (`image/jpeg`).
- Produces (API): `GET /v1/documents?status=<один из 5>&limit=1..100 (50)&before=<ISO datetime>` →
  `{items: [{id, status, schema: {slug, version}, title, summary: [{field, value}], issue: str|null,
  created_at, finished_at}], next_before: ISO|null}`; новые сверху; `title` = `data.number`, если есть
  непустой, иначе первые 8 символов id; `summary` — до 3 первых непустых скалярных полей `data` (строки/числа,
  в порядке ключей, без `number`); `issue` — сообщение первого замечания с кодом не из `INFO_CODES`.
  Неверный `status`/`before` → 422.
- Produces (API): `GET /v1/documents/{id}/pages/{n}` → `image/jpeg`; чужой/нет документа/нет файла → 404.
- Produces (API): `GET /v1/stats?days=1..365 (30)` → `{days, total, by_status: {queued, processing, ok,
  needs_review, failed}, auto_share: float|null, reviewed: int}`; `auto_share` = ok без ручной проверки /
  (ok + needs_review + failed) за период, `null` при нуле; `reviewed` = документы с `reviewed_at` за период
  (колонка появится в Task 4 — до неё `reviewed` = 0, тест на это — в Task 4).

- [ ] **Step 1: Тесты** — `test_list_newest_first_and_pagination` (3 документа, limit=2 → 2 и `next_before`,
  второй запрос — оставшийся), `test_list_filter_status`, `test_list_isolated_between_tenants`,
  `test_list_item_shape` (title из `number`, summary ≤ 3, issue — первое не-информационное), `test_list_bad_status_422`,
  `test_page_preview_served` (кладём blob по `page_key` → 200 `image/jpeg`), `test_page_preview_missing_404`,
  `test_page_preview_foreign_404`, `test_stats_counts`; в `tests/test_pipeline.py`
  `test_pipeline_stores_page_previews` (после обработки есть `page_key(id, 1)`).
- [ ] **Step 2:** Падают. **Step 3:** Реализовать. **Step 4:** Зелёные + ruff.
- [ ] **Step 5:** Commit `feat: список документов, статистика и превью страниц`.

### Task 4: Ручная проверка, ключи API, подсказки справочников (бэкенд)

**Files:**
- Modify: `src/docflow/db/models.py`, `src/docflow/api/documents.py`, `src/docflow/serializers.py`,
  `src/docflow/api/dictionaries.py`, `src/docflow/validation/dictionaries.py`, `src/docflow/api/app.py`
- Create: `migrations/versions/0005_review.py`, `src/docflow/api/apikeys.py`, `src/docflow/review.py`
- Test: `tests/api/test_review_api.py`, `tests/api/test_apikeys_api.py`, `tests/api/test_dictionaries_api.py`

**Interfaces:**
- Consumes: `Principal`, `require_owner` (Task 1); `normalize`, `resolve_dictionaries`, `evaluate`,
  `_load_dictionaries` из `pipeline` (вынести в публичную `load_dictionaries(session, tenant_id, schema)`),
  `enqueue_webhooks`.
- Produces (колонки `documents`): `reviewed_by: UUID|None → users SET NULL`, `reviewed_at|None`, `review_note: str|None`.
- Produces (`docflow.review`): `async review_document(session, *, document_id, tenant_id, principal, decision,
  data: dict|None, note: str|None, schemas: SchemaResolver) -> Document` — атомарный UPDATE
  `WHERE status='needs_review'`; 0 строк → `ReviewConflict`; approve: если `data` передан — `normalize(schema, data)`
  + `resolve_dictionaries` + `evaluate`, сохранить данные и оставшиеся замечания, статус `ok`; reject — статус
  `failed`, `error = "отклонён при проверке" + (": " + note если есть)`.
- Produces (API): `POST /v1/documents/{id}/review {decision: "approve"|"reject", data?: object, note?: ≤1000}` →
  200 `document_payload`; 404 чужой/нет; 409 `{"detail": "документ уже проверен или ещё обрабатывается"}`;
  после успеха — вебхуки (`document.completed` для approve, `document.failed` для reject).
- Produces (`document_payload`): новое поле `review: {at, note} | null`.
- Produces (API, только сессия владельца): `GET /v1/api-keys` → `[{id, prefix, created_at, revoked_at}]`;
  `POST /v1/api-keys` → 201 `{id, prefix, key, created_at}`; `DELETE /v1/api-keys/{id}` → 204 (ставит
  `revoked_at`); чужой → 404; ключ API или оператор → 403.
- Produces (`docflow.validation.dictionaries`): `nearest(raw: str, entries) -> str | None` (лучшее
  совпадение со скелетной похожестью ≥ `SUGGEST_MIN`).
- Produces (API): `GET /v1/dictionaries/{name}/suggestions` → `[{value, code, count, nearest}]`, по убыванию
  `count`, не больше 50; источник — замечания `not_in_dictionary` и `corrected` последних 200 документов
  тенанта, у которых сообщение ссылается на справочник `«{name}»` (разбор сообщений, формат которых задаёт
  `resolve_dictionaries`; тест фиксирует формат); для `corrected` `nearest` = значение, на которое исправили.

- [ ] **Step 1: Тесты** — `test_review_approve_sets_ok_and_webhook`, `test_review_reject_sets_failed_with_note`,
  `test_review_twice_conflict` (второй вызов → 409), `test_review_not_needs_review_409`,
  `test_review_approve_normalizes_edits` (`{"total": "12 500,50", "delivery": "24.10"}` при дате документа
  2026-10-20 → 12500.5 и `2026-10-24`), `test_review_reports_remaining_issues` (правка, ломающая сумму,
  → статус ok, но замечание в `issues`), `test_review_foreign_404`, `test_review_records_user`
  (сессия → `reviewed_by`), `test_stats_reviewed_counts`;
  ключи: `test_apikeys_owner_crud`, `test_apikeys_operator_403`, `test_apikeys_key_auth_403`,
  `test_revoked_key_stops_working`; справочники: `test_suggestions_from_recent_documents`,
  `test_suggestions_isolated`.
- [ ] **Step 2:** Падают. **Step 3:** Реализовать (миграция `0005`). **Step 4:** Зелёные + ruff + `tests/db`.
- [ ] **Step 5:** Commit `feat: ручная проверка документов, ключи API из кабинета, подсказки справочников`.

### Task 5: Каркас сайта: Next.js, дизайн-система, клиент API, Docker

**Files:**
- Create: `web/` (Next.js 15, App Router, `src/`, TypeScript strict, Tailwind v4, ESLint), `web/src/app/globals.css`
  (токены через `@theme`), `web/src/app/layout.tsx` (шрифты `next/font/google`: Inter Tight 400/500/600 и
  JetBrains Mono 400/500, `subsets: ['latin','cyrillic']`), `web/src/components/ui/*`,
  `web/src/lib/api.ts`, `web/src/lib/session.ts`, `web/src/middleware.ts`, `web/Dockerfile`,
  `web/.env.example`, `web/README.md`
- Modify: `docker-compose.yml` (сервис `web`, `DOCFLOW_INTERNAL_TOKEN` и для `api`, и для `web`),
  `.gitignore` (`web/node_modules`, `web/.next`)

**Interfaces:**
- Produces (`web/src/lib/api.ts`, `import "server-only"`): `class ApiError(status: number, detail: string)`;
  `apiFetch<T>(path: string, init?: RequestInit & {auth?: boolean /* default true */}): Promise<T>` — добавляет
  заголовки из Global Constraints, `cache: "no-store"`, 204 → `undefined`, не-2xx → `ApiError` с `detail` из тела;
  `apiFetchRaw(path, init?) : Promise<Response>` (для картинок/PDF).
- Produces (`web/src/lib/session.ts`): `SESSION_COOKIE = "df_session"`, `getSessionToken(): Promise<string|null>`,
  `setSession(token, expiresAt)`, `clearSession()`; `requireMe(): Promise<Me>` — `GET /v1/me`, 401 → очистка
  cookie и `redirect("/login")`.
- Produces (`web/src/lib/types.ts`): типы ответов API ровно по Interfaces задач 1–4 (`Me`, `DocumentListItem`,
  `DocumentPayload`, `Stats`, `Dictionary`, `Suggestion`, `ApiKey`, `Webhook`).
- Produces (`middleware.ts`): для `/app/:path*` без cookie → `redirect /login?next=<path>`.
- Produces (UI-примитивы `web/src/components/ui/`): `Button` (варианты `primary` чёрный, `accent` красный,
  `ghost` с рамкой; `href` → ссылка), `Field` (label + input/textarea/select + текст ошибки, `aria-describedby`),
  `Section` (нумерация `[NN]`, заголовок, мета-метка), `StatusBadge` (ok/needs_review/failed/queued/processing,
  квадратная метка, подпись по-русски), `Stat` (крупная моно-цифра + подпись), `Logo` («DocFlow®»).
- Docker: многоэтапный `node:22-alpine`, `output: "standalone"`, пользователь не root, порт 3000; в compose
  `web` зависит от `api`, порт `3000:3000`, env `DOCFLOW_API_URL=http://api:8000`, `DOCFLOW_INTERNAL_TOKEN`,
  `COOKIE_SECURE=false` (dev).

- [ ] **Step 1:** Создать проект (`npx create-next-app@15 web --ts --tailwind --eslint --app --src-dir
  --import-alias "@/*" --use-npm --no-turbopack`), версии зафиксировать в `package.json`; `output: "standalone"`.
- [ ] **Step 2:** Токены и примитивы по Global Constraints; страница-заглушка `/` с примитивами.
- [ ] **Step 3:** `api.ts`, `session.ts`, `types.ts`, `middleware.ts`.
- [ ] **Step 4:** Проверка: `npm run lint`, `npx tsc --noEmit`, `npm run build` — без ошибок;
  `docker compose build web` и `docker compose up -d web` → `curl -s localhost:3000` отдаёт HTML.
- [ ] **Step 5:** Commit `feat(web): каркас Next.js, дизайн-система и клиент API`.

### Task 6: Публичные страницы: лендинг, аудит, политика, 404

**Files:**
- Create: `web/src/app/page.tsx`, `web/src/components/landing/*` (по блоку на файл), `web/src/app/audit/page.tsx`,
  `web/src/app/audit/actions.ts`, `web/src/components/AuditForm.tsx`, `web/src/app/privacy/page.tsx`,
  `web/src/app/not-found.tsx`, `web/src/app/opengraph-image.tsx` (необязательно)

**Interfaces:**
- Consumes: примитивы и `apiFetch` (Task 5), `POST /v1/leads` (Task 2).
- Produces: `submitLead(prev: LeadState, form: FormData): Promise<LeadState>` (server action, `useActionState`),
  `LeadState = {ok: boolean; errors?: Record<string,string>; message?: string}`. Форма работает без JS
  (обычный POST в action), поля: имя, компания, контакт (телефон/Telegram/email), ниша (select: зуботехническая
  лаборатория, бухгалтерия и аутсорс, логистика и склад, автосервис, медицина, кадры, другое), объём в месяц
  (select по `volume`), какие документы, сообщение, согласие (checkbox, ссылка на `/privacy`), скрытое поле
  `website` (honeypot, вне экрана, `tabindex=-1`, `autocomplete=off`). Успех → экран «Заявка принята» с
  `[СРОК ОТВЕТА]`; 429 → «Слишком много заявок с вашего адреса, попробуйте позже».
- Лендинг: блоки 1–13 из спецификации (раздел «Лендинг: блоки»), якоря `#how`, `#niches`, `#security`,
  `#integrations`, `#faq`, `#audit`. Блок «Измерено» — ровно факты прогона 28.10.2026: 10 рукописных
  заказ-нарядов, 100% полей верно, 7 из 10 без участия человека, 3 отправлены на проверку и во всех трёх
  расхождение было в самом документе (в одном — итог 21 000 при сумме строк 20 000).
  Интеграции 1С/Битрикс24/amoCRM помечены «скоро».

- [ ] **Step 1:** Использовать скилл `frontend-design:frontend-design` для визуального решения в рамках токенов.
- [ ] **Step 2:** Сверстать страницы; адаптив от 360px без горизонтальной прокрутки.
- [ ] **Step 3:** Проверка: lint, tsc, build зелёные; вручную через `docker compose up -d --build web`:
  `/`, `/audit`, `/privacy`, `/несуществующая` отдают 200/200/200/404; отправка формы создаёт заявку
  (`docker compose exec api docflow leads`).
- [ ] **Step 4:** Commit `feat(web): лендинг, форма аудита, политика ПД`.

### Task 7: Вход и оболочка кабинета

**Files:**
- Create: `web/src/app/login/page.tsx`, `web/src/app/login/actions.ts`, `web/src/app/app/layout.tsx`,
  `web/src/app/app/logout/route.ts` (POST), `web/src/components/app/AppShell.tsx`

**Interfaces:**
- Consumes: `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/me`, `requireMe`, `setSession`, `clearSession`.
- Produces: `login(prev, form) -> {error?: string}` — успех → `setSession` и `redirect(next || "/app")`, где `next`
  принимается только как относительный путь, начинающийся с `/app` (защита от open redirect); 401 → «Неверный
  email или пароль»; 429 → текст API. `AppShell`: верхняя полоса (логотип, имя клиента, режим модели, выход),
  навигация: Очередь `/app`, Загрузить `/app/upload`, Справочники `/app/dictionaries`, Интеграции `/app/settings`.
  `/app/layout.tsx` вызывает `requireMe()`.

- [ ] **Step 1:** Реализовать. **Step 2:** lint/tsc/build зелёные; вручную: пользователь из
  `docflow create-user` входит, неверный пароль показывает ошибку, `/app` без cookie → `/login?next=/app`,
  выход стирает cookie и ведёт на `/login`, `?next=https://evil` → после входа `/app`.
- [ ] **Step 3:** Commit `feat(web): вход и оболочка кабинета`.

### Task 8: Кабинет: очередь и проверка документа

**Files:**
- Create: `web/src/app/app/page.tsx`, `web/src/app/app/documents/[id]/page.tsx`,
  `web/src/app/app/documents/[id]/actions.ts`, `web/src/components/app/ReviewForm.tsx`,
  `web/src/app/app/files/[id]/pdf/route.ts`, `web/src/app/app/files/[id]/pages/[n]/route.ts`

**Interfaces:**
- Consumes: `GET /v1/stats`, `GET /v1/documents?…`, `GET /v1/documents/{id}`, `GET /v1/schemas/{slug}?version=`
  (существующий), `GET /v1/documents/{id}/pages/{n}`, `GET /v1/documents/{id}/pdf`, `POST /v1/documents/{id}/review`.
- Produces: очередь — цифры `Stat` (Обработано, Без человека %, На проверке, Ошибки), фильтр статусов ссылками
  `?status=`, таблица (`title`, summary, `issue` красным, статус, время), «Показать ещё» по `next_before`,
  кнопка «Открыть следующий на проверку» (первый `needs_review`).
- Produces: страница документа — превью страниц через Route Handlers (`<img>` с `onError` → плашка «превью
  недоступно — скачайте PDF»), замечания с указанием поля, форма полей по схеме (строка/число/дата — input
  с исходным значением как текст; enum — select; boolean — checkbox; таблица — строки и колонки), кнопки
  «Подтвердить и отправить» и «Отклонить» (с полем причины). `approve(id, form)`/`reject(id, form)` — server
  actions: собирают `data` по схеме (пустое → null; таблица — массив строк), 409 → сообщение «Документ уже
  проверен» и ссылка в очередь; успех → редирект на следующий `needs_review` или `/app`.
  Для статусов кроме `needs_review` — только просмотр.

- [ ] **Step 1:** Реализовать. **Step 2:** lint/tsc/build; вручную на стеке: загрузить через API документ
  с ошибкой суммы (fake-экстрактор или реальный), открыть, поправить итог, подтвердить → статус ok в очереди.
- [ ] **Step 3:** Commit `feat(web): очередь и проверка документа`.

### Task 9: Кабинет: загрузка, справочники, интеграции

**Files:**
- Create: `web/src/app/app/upload/page.tsx` + `actions.ts`, `web/src/app/app/dictionaries/page.tsx`,
  `web/src/app/app/dictionaries/[name]/page.tsx` + `actions.ts`, `web/src/components/app/DictionaryEditor.tsx`,
  `web/src/app/app/settings/page.tsx` + `actions.ts`

**Interfaces:**
- Consumes: `GET /v1/schemas`, `POST /v1/documents` (multipart: `schema`, `files[]`, `mode`; заголовок
  `Idempotency-Key` = случайный UUID на каждую отправку формы), `GET/PUT/DELETE /v1/dictionaries…`,
  `GET /v1/dictionaries/{name}/suggestions`, `GET/POST/DELETE /v1/api-keys`, `GET/POST/DELETE /v1/webhooks`, `GET /v1/me`.
- Produces: загрузка — выбор схемы, файлы (перетаскивание + input, до 30 файлов, предупреждение > 20 МБ на
  файл), режим (Серый/Цветной/Ч-Б); успех → ссылка на документ. Справочники — список с размерами; создание
  (имя `^[a-z0-9_]{2,40}$` + название); редактор (значение + написания через запятую, добавить/удалить строку,
  «Сохранить» → PUT целиком, ошибки 422 показываются у формы), блок подсказок с кнопками «Добавить как
  написание к «nearest»» и «Добавить новым значением». Интеграции — ключи API (создать: ключ показывается один
  раз с кнопкой копирования, отозвать), вебхуки (добавить URL и события, секрет один раз; удалить), пример
  `curl` загрузки с подставленным базовым адресом API, блок аккаунта (email, клиент, модель, `auto_ok`) и выход.
  Для оператора (не владельца) раздел ключей скрыт с пояснением.

- [ ] **Step 1:** Реализовать. **Step 2:** lint/tsc/build; вручную: загрузка файла создаёт документ,
  справочник сохраняется и перечитывается, ключ создаётся и отзывается.
- [ ] **Step 3:** Commit `feat(web): загрузка, справочники и интеграции в кабинете`.

### Task 10: Сквозные тесты сайта, README, финальная сборка

**Files:**
- Create: `web/playwright.config.ts`, `web/e2e/site.spec.ts`, `scripts/seed-demo.ps1` (необязательно)
- Modify: `README.md` (раздел «Сайт и кабинет»), `docker-compose.e2e.yml` (если нужно для web)

**Interfaces:**
- Consumes: всё выше; стек `docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build`
  (fake-экстрактор).

- [ ] **Step 1: Тесты Playwright** (`baseURL http://localhost:3000`, перед тестами — `create-tenant` и
  `create-user` через `docker compose exec`): `landing renders key sections` (h1, якоря, форма),
  `audit form submits` (заполнить, отправить → «Заявка принята»), `audit form requires consent`,
  `anonymous app redirects to login`, `login wrong password shows error`,
  `login → queue → review approve` (загрузить документ через API с ключом клиента, дождаться `needs_review`
  из fake-ответа с несходящейся суммой, открыть, подтвердить → статус «Готово»), `logout clears session`,
  `revoked session redirects to login` (удалить сессию в БД/через logout API и обновить страницу).
- [ ] **Step 2:** Прогнать против стека — зелёные.
- [ ] **Step 3:** README: как поднять сайт, создать клиента и пользователя, где форма заявок и уведомления.
- [ ] **Step 4:** Commit `test(web): сквозные тесты сайта; docs: README сайта`.
