"use client";

import { useActionState } from "react";

import { createWebhook, type WebhookState } from "@/app/app/settings/actions";
import { SecretBox } from "@/components/app/SecretBox";
import { Button, Field } from "@/components/ui";

const INITIAL: WebhookState = {};

const EVENTS = [
  { value: "document.completed", label: "Документ обработан", on: true },
  { value: "document.failed", label: "Ошибка обработки", on: true },
  { value: "document.reviewed", label: "Подтверждён человеком", on: false },
];

/** Добавление вебхука: адрес + события; секрет подписи показывается один раз. */
export function CreateWebhookForm() {
  const [state, action, pending] = useActionState(createWebhook, INITIAL);
  const urlError = state.error && !/событие/.test(state.error) ? state.error : undefined;
  const eventsError = state.error && /событие/.test(state.error) ? state.error : undefined;
  return (
    <form action={action} aria-busy={pending} className="flex flex-col gap-4 border-2 border-ink bg-card p-4">
      {state.secret ? (
        <SecretBox
          title="Секрет подписи вебхука"
          value={state.secret}
          note="Секрет показывается один раз. Им проверяется заголовок X-DocFlow-Signature у входящих запросов."
        />
      ) : null}
      <Field
        label="Адрес (https://…)"
        name="url"
        type="url"
        required
        defaultValue={state.url}
        autoComplete="off"
        placeholder="https://example.com/hook"
        error={urlError}
        className="font-mono"
      />
      <fieldset className="flex flex-col gap-1" aria-describedby={eventsError ? "ev-err" : undefined}>
        <legend className="mb-1 font-mono text-sm font-medium">События</legend>
        {EVENTS.map((e) => (
          <label key={e.value} className="inline-flex min-h-11 items-center gap-3">
            <input type="checkbox" name="events" value={e.value} defaultChecked={e.on} className="size-5 accent-ink" />
            <span>
              {e.label} <span className="font-mono text-sm text-muted">{e.value}</span>
            </span>
          </label>
        ))}
        {eventsError ? (
          <p id="ev-err" role="alert" className="text-sm font-medium text-accent-text">
            {eventsError}
          </p>
        ) : null}
      </fieldset>
      <div>
        <Button type="submit" disabled={pending}>
          {pending ? "Добавляем…" : "Добавить вебхук"}
        </Button>
      </div>
    </form>
  );
}
