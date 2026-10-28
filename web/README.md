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

## Переменные окружения

| Переменная | Назначение |
| --- | --- |
| `DOCFLOW_API_URL` | адрес API |
| `DOCFLOW_INTERNAL_TOKEN` | секрет для `X-Internal-Token`; тот же, что `internal_token` у API |
| `COOKIE_SECURE` | `false` отключает флаг Secure у cookie в dev |

## Дизайн-система

Токены — в `src/app/globals.css` (`@theme`), примитивы — в `src/components/ui/`
(`Button`, `Field`, `Section`, `StatusBadge`, `Stat`, `Logo`).
