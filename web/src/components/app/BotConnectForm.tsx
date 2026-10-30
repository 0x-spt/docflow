"use client";

import { useState, useTransition } from "react";

import { connectBot } from "@/app/app/bot/actions";
import { Button, Field } from "@/components/ui";

/** Подключение бота: только токен от @BotFather. Токен уходит в server action и после успеха стирается из поля. */
export function BotConnectForm() {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  return (
    <form
      aria-busy={pending}
      onSubmit={(e) => {
        e.preventDefault();
        if (pending) return;
        if (!token.trim()) {
          setError("Вставьте токен бота.");
          return;
        }
        setError(null);
        start(async () => {
          const res = await connectBot(token.trim());
          if (res.error) setError(res.error);
          else setToken("");
        });
      }}
      className="flex max-w-xl flex-col gap-4"
    >
      <Field
        label="Токен бота"
        name="bot-token"
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={token}
        onChange={(e) => setToken(e.target.value)}
        hint="Выглядит как 123456:ABC… — его присылает @BotFather. Токен хранится в зашифрованном виде и больше нигде не показывается."
        error={error ?? undefined}
      />
      <div>
        <Button type="submit" disabled={pending}>
          {pending ? "Подключаем…" : "Подключить"}
        </Button>
      </div>
    </form>
  );
}
