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
| `DOCFLOW_API_URL` | адрес API, по которому ходит сервер сайта (в compose — `http://api:8000`) |
| `DOCFLOW_PUBLIC_API_URL` | публичный адрес API для примеров в кабинете (то, что клиент вписывает в интеграцию) |
| `DOCFLOW_INTERNAL_TOKEN` | секрет для `X-Internal-Token`; тот же, что `DOCFLOW_INTERNAL_TOKEN` у API. Пусто — адрес посетителя API не передаётся как доверенный |
| `COOKIE_SECURE` | `false` отключает флаг Secure у cookie в dev; в проде за https — `true` |

Адрес посетителя сайт берёт из `X-Real-IP` (его обязан ставить обратный прокси: `proxy_set_header X-Real-IP
$remote_addr;`), без него — из последнего адреса `X-Forwarded-For`.

## Дизайн-система

Токены — в `src/app/globals.css` (`@theme`), примитивы — в `src/components/ui/`
(`Button`, `Field`, `Section`, `StatusBadge`, `Stat`, `Logo`).
