"use client";

import { useActionState } from "react";

import { createDictionary, type DictState } from "@/app/app/dictionaries/actions";
import { Button, Field } from "@/components/ui";

const INITIAL: DictState = {};

/** Создание справочника: имя (латиница, на него ссылается схема) и название. */
export function CreateDictionaryForm() {
  const [state, action, pending] = useActionState(createDictionary, INITIAL);
  const nameError = state.error && /Имя|уже есть/.test(state.error) ? state.error : undefined;
  const otherError = state.error && !nameError ? state.error : undefined;
  return (
    <form action={action} aria-busy={pending} className="grid gap-4 border-2 border-ink bg-card p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-start">
      <Field
        label="Имя"
        name="name"
        defaultValue={state.name}
        required
        pattern="[a-z0-9_]{2,40}"
        autoComplete="off"
        placeholder="clinics"
        hint="латиница, цифры, «_»; на него ссылается схема"
        error={nameError}
        className="font-mono"
      />
      <Field label="Название" name="title" defaultValue={state.title} required maxLength={200} autoComplete="off" placeholder="Клиники" error={otherError} />
      <div className="sm:pt-[1.625rem]">
        <Button type="submit" disabled={pending}>
          {pending ? "Создаём…" : "Создать"}
        </Button>
      </div>
    </form>
  );
}
