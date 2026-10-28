"use client";

import { useActionState } from "react";

import { createKey, type KeyState } from "@/app/app/settings/actions";
import { SecretBox } from "@/components/app/SecretBox";
import { Button } from "@/components/ui";

const INITIAL: KeyState = {};

/** Кнопка «Выпустить ключ»; новый ключ показывается здесь один раз и исчезает при уходе со страницы. */
export function CreateKeyForm() {
  const [state, action, pending] = useActionState(async () => createKey(), INITIAL);
  return (
    <form action={action} aria-busy={pending} className="flex flex-col gap-4">
      {state.key ? (
        <SecretBox
          title={`Новый ключ ${state.prefix ?? ""}…`}
          value={state.key}
          note="Ключ показывается один раз: сохраните его сейчас. После ухода со страницы увидеть его снова нельзя — только выпустить новый."
        />
      ) : null}
      {state.error ? (
        <p role="alert" className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text">
          {state.error}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={pending}>
          {pending ? "Выпускаем…" : "Выпустить ключ"}
        </Button>
      </div>
    </form>
  );
}
