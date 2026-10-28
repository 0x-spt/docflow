"use client";

import "./globals.css";

/**
 * Последний рубеж: ошибка в корневом макете или макете кабинета (например, API недоступен при проверке сессии).
 * Заменяет весь документ, поэтому сам рисует <html> и <body>.
 */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="ru">
      <body className="min-h-screen antialiased">
        <main role="alert" className="mx-auto flex max-w-3xl flex-col items-start gap-4 px-4 py-16 sm:py-28">
          <p className="font-mono text-sm uppercase text-accent-text">Ошибка</p>
          <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Что-то пошло не так</h1>
          <p className="max-w-xl text-lg text-muted">
            Сервис временно недоступен. Попробуйте ещё раз через минуту.
          </p>
          <div className="flex flex-col gap-4 sm:flex-row">
            <button
              type="button"
              onClick={() => reset()}
              className="inline-flex min-h-11 items-center justify-center border-2 border-ink bg-ink px-5 py-2.5 font-semibold text-paper shadow-hard active:translate-x-1.5 active:translate-y-1.5 active:shadow-none"
            >
              Повторить
            </button>
            {/* обычная ссылка: роутер Next здесь может быть неработоспособен */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a
              href="/"
              className="inline-flex min-h-11 items-center justify-center border-2 border-ink bg-card px-5 py-2.5 font-semibold shadow-hard"
            >
              На главную
            </a>
          </div>
        </main>
      </body>
    </html>
  );
}
