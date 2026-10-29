"use client";

import { startTransition, useActionState, useEffect, useRef, useState, type FormEvent } from "react";

import { saveLabels, type LabelsState } from "@/app/app/fields/actions";
import { Button, Field } from "@/components/ui";
import type { FieldLabelRow } from "@/lib/types";

const INITIAL: LabelsState = {};
const MAX_QUEUE = 3;

/**
 * Названия полей и состав очереди. Строки: сначала поля верхнего уровня, затем каждая таблица своей группой.
 * «Сохранить» заменяет набор целиком; пустое название — название по умолчанию.
 */
export function FieldLabelsForm({
  slug,
  rows,
  readOnly,
}: {
  slug: string;
  rows: FieldLabelRow[];
  readOnly: boolean;
}) {
  const [state, action, pending] = useActionState(saveLabels.bind(null, slug), INITIAL);
  const [queue, setQueue] = useState<string[]>(() => rows.filter((r) => r.queue).map((r) => r.key));
  const noteRef = useRef<HTMLDivElement>(null);
  const errors = state.fieldErrors ?? {};

  useEffect(() => {
    if (state.error || state.saved) noteRef.current?.focus();
  }, [state]);

  // отправка вручную: автосброс формы React 19 стёр бы правки при ошибке 422
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || readOnly) return;
    const form = new FormData(event.currentTarget);
    startTransition(() => action(form));
  }

  const top = rows.filter((r) => r.group === null);
  const groups = new Map<string, FieldLabelRow[]>();
  for (const r of rows) {
    if (r.group !== null) groups.set(r.group, [...(groups.get(r.group) ?? []), r]);
  }
  const titleOf = (name: string) => top.find((r) => r.key === name)?.default ?? name;

  function renderRow(r: FieldLabelRow) {
    const queueable = r.group === null && r.type !== "table";
    const checked = queue.includes(r.key);
    return (
      <tr key={r.key} className="border-t border-ink/30 align-top">
        <td className="px-3 py-2.5">
          <div className="font-medium">{r.default}</div>
          <div className="font-mono text-xs text-muted">{r.key}</div>
        </td>
        <td className="px-1 py-1.5">
          <Field
            label={`Ваше название: ${r.default}`}
            name={`label:${r.key}`}
            defaultValue={r.label}
            placeholder={r.default}
            maxLength={80}
            autoComplete="off"
            error={errors[r.key]}
          />
        </td>
        <td className="px-3 py-2.5">
          {queueable ? (
            <label className="inline-flex min-h-11 items-center gap-2">
              <input
                type="checkbox"
                name="queue"
                value={r.key}
                checked={checked}
                disabled={!checked && queue.length >= MAX_QUEUE}
                onChange={(e) => setQueue((q) => (e.target.checked ? [...q, r.key] : q.filter((k) => k !== r.key)))}
                className="size-5 accent-ink"
              />
              <span className="sr-only">В очереди: {r.default}</span>
            </label>
          ) : null}
        </td>
      </tr>
    );
  }

  function renderTable(caption: string | null, list: FieldLabelRow[]) {
    return (
      <div className="relative overflow-x-auto border-2 border-ink bg-card">
        <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
          {caption ? (
            <caption className="border-b-2 border-ink bg-paper px-3 py-2 text-left font-semibold">{caption}</caption>
          ) : null}
          <thead className="bg-ink text-paper">
            <tr>
              <th scope="col" className="min-w-48 px-3 py-2 font-mono font-medium">
                Поле
              </th>
              <th scope="col" className="min-w-64 px-3 py-2 font-mono font-medium">
                Ваше название
              </th>
              <th scope="col" className="w-28 px-3 py-2 font-mono font-medium">
                В очереди
              </th>
            </tr>
          </thead>
          <tbody>{list.map(renderRow)}</tbody>
        </table>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} aria-busy={pending} className="flex flex-col gap-4">
      <fieldset disabled={readOnly} className="flex min-w-0 flex-col gap-4">
        <legend className="sr-only">Названия полей</legend>
        {state.error ? (
          <div
            ref={noteRef}
            tabIndex={-1}
            role="alert"
            className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text"
          >
            {state.error}
          </div>
        ) : state.saved ? (
          <div ref={noteRef} tabIndex={-1} role="status" className="border-2 border-ink bg-card p-4 font-medium">
            Сохранено.
          </div>
        ) : null}

        {errors.queue_fields ? (
          <p role="alert" className="text-sm font-medium text-accent-text">
            В очереди: {errors.queue_fields}
          </p>
        ) : null}
        <p className="font-mono text-sm text-muted">
          В очереди отмечено: {queue.length} из {MAX_QUEUE}
        </p>

        {renderTable(null, top)}
        {[...groups].map(([name, list]) => (
          <section key={name} aria-label={`Таблица «${titleOf(name)}»`}>
            {renderTable(`Таблица «${titleOf(name)}», колонки`, list)}
          </section>
        ))}

        <div>
          <Button type="submit" disabled={pending || readOnly}>
            {pending ? "Сохраняем…" : "Сохранить"}
          </Button>
        </div>
      </fieldset>
    </form>
  );
}
