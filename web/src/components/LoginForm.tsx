"use client";

import { useActionState, useEffect, useRef } from "react";

import { login, type LoginState } from "@/app/login/actions";
import { Button, Field } from "@/components/ui";

const INITIAL: LoginState = {};

/** Форма входа. Без JS — обычная отправка в server action; с JS — состояние «Входим…» и фокус на ошибке. */
export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(login, INITIAL);
  const alertRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (state.error) alertRef.current?.focus();
  }, [state]);

  return (
    // key: после ответа сервера поля пересоздаются (email сохраняется, пароль — нет)
    <form
      key={JSON.stringify(state)}
      action={action}
      aria-busy={pending}
      className="grid gap-6 border-2 border-ink bg-card p-5 shadow-hard sm:p-8"
    >
      <input type="hidden" name="next" value={next} />
      {state.error ? (
        <div
          ref={alertRef}
          tabIndex={-1}
          role="alert"
          className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text"
        >
          {state.error}
        </div>
      ) : null}
      <Field label="Email" name="email" type="email" autoComplete="email" required defaultValue={state.email} />
      <Field label="Пароль" name="password" type="password" autoComplete="current-password" required />
      <Button type="submit" variant="accent" disabled={pending} className="w-full sm:w-auto">
        {pending ? "Входим…" : "Войти"}
      </Button>
    </form>
  );
}
