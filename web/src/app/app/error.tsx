"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui";

/** Ошибка внутри кабинета (чаще всего API недоступен): по-русски и с повтором вместо стандартной 500. */
export default function CabinetError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("кабинет не загрузился", error);
  }, [error]);

  return (
    <div role="alert" className="flex flex-col items-start gap-4 py-8">
      <h1 className="text-4xl font-semibold tracking-tight">Не удалось загрузить кабинет</h1>
      <p className="max-w-xl text-muted">
        Сервис временно недоступен или не ответил вовремя. Данные не потеряны — попробуйте ещё раз через минуту.
      </p>
      <div className="flex flex-col gap-4 sm:flex-row">
        <Button variant="primary" onClick={() => reset()}>
          Повторить
        </Button>
        <Button href="/" variant="ghost">
          На главную
        </Button>
      </div>
    </div>
  );
}
