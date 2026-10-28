# DocFlow: сайт

Next.js 15 (App Router, `src/`), TypeScript strict, Tailwind v4. Браузер токена не видит: вход, cookie
`df_session` (httpOnly) и все запросы к API идут с сервера (`src/lib/api.ts`, `src/lib/session.ts`).

## Запуск

```bash
cp .env.example .env.local
npm ci
npm run dev        # http://localhost:3000
```

API должен быть доступен по `DOCFLOW_API_URL` (`docker compose up -d api` в корне репозитория).
Весь стек вместе с сайтом: `docker compose up -d --build` — сайт на http://localhost:3000.

## Проверки

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## Сквозные тесты (Playwright)

Нужен стек с fake-экстрактором (ответы — `tests/e2e/fake_responses.json`, ключ `e2e_order` только для этих тестов):

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build   # из корня репозитория
cd web
npx playwright install chromium
npm run test:e2e
docker compose up -d        # потом: вернуть обычный (не fake) воркер
```

Браузеры Playwright тяжёлые; чтобы не занимать диск C:, задайте кэш на другом диске в обеих командах
(`install` и `test:e2e`): PowerShell — `$env:PLAYWRIGHT_BROWSERS_PATH = "E:\dev\ms-playwright"`,
bash — `export PLAYWRIGHT_BROWSERS_PATH='E:\dev\ms-playwright'`.

Перед тестами `e2e/global-setup.ts` через `docker compose exec api docflow` создаёт клиента и владельца с уникальным
email, регистрирует схему `e2e_order`, загружает пустую картинку и ждёт `needs_review` (итог в заготовке не
сходится с суммой строк), поэтому тесты можно запускать повторно. Адреса: `E2E_BASE_URL` (сайт, по умолчанию
http://localhost:3000) и `E2E_API_URL` (API, http://localhost:8000).

## Переменные окружения

| Переменная | Назначение |
| --- | --- |
| `DOCFLOW_API_URL` | адрес API |
| `DOCFLOW_INTERNAL_TOKEN` | секрет для `X-Internal-Token`; тот же, что `internal_token` у API |
| `COOKIE_SECURE` | `false` отключает флаг Secure у cookie в dev |

## Дизайн-система

Токены — в `src/app/globals.css` (`@theme`), примитивы — в `src/components/ui/`
(`Button`, `Field`, `Section`, `StatusBadge`, `Stat`, `Logo`).
