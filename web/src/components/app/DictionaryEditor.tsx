"use client";

import { startTransition, useActionState, useEffect, useRef, useState, type FormEvent } from "react";

import { saveDictionary, type DictState } from "@/app/app/dictionaries/actions";
import { Button, Field } from "@/components/ui";
import type { DictionaryEntry } from "@/lib/types";

const INITIAL: DictState = {};
const CELL =
  "min-h-11 w-full border-2 border-ink bg-card px-2 py-1.5 font-sans text-base text-ink " +
  "aria-[invalid=true]:border-accent-text";

type Row = { key: number; value: string; aliases: string };

/** Редактор справочника: строка = значение + написания через запятую; «Сохранить» заменяет справочник целиком. */
export function DictionaryEditor({
  name,
  title,
  entries,
}: {
  name: string;
  title: string;
  entries: DictionaryEntry[];
}) {
  const [state, action, pending] = useActionState(saveDictionary.bind(null, name), INITIAL);
  const next = useRef(entries.length);
  const [rows, setRows] = useState<Row[]>(() =>
    entries.map((e, i) => ({ key: i, value: e.value, aliases: e.aliases.join(", ") })),
  );
  const noteRef = useRef<HTMLDivElement>(null);
  const lastRow = useRef<HTMLInputElement>(null);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    if (state.error || state.saved) noteRef.current?.focus();
  }, [state]);

  useEffect(() => {
    if (added) {
      lastRow.current?.focus();
      setAdded(false);
    }
  }, [added]);

  // отправка вручную: автосброс формы React 19 стёр бы правки при ошибке 422
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = new FormData(event.currentTarget);
    startTransition(() => action(form));
  }

  function update(key: number, patch: Partial<Row>) {
    setRows((r) => r.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  }

  return (
    <form onSubmit={onSubmit} aria-busy={pending} className="flex flex-col gap-4">
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
          Справочник сохранён. Новые документы сверяются с ним сразу.
        </div>
      ) : null}

      <div className="max-w-xl">
        <Field label="Название" name="title" defaultValue={title} required maxLength={200} autoComplete="off" />
      </div>

      <div className="relative overflow-x-auto border-2 border-ink bg-card">
        <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
          <thead className="bg-ink text-paper">
            <tr>
              <th scope="col" className="w-10 px-2 py-2 font-mono font-medium">
                №
              </th>
              <th scope="col" className="min-w-52 px-2 py-2 font-mono font-medium">
                Значение
              </th>
              <th scope="col" className="min-w-64 px-2 py-2 font-mono font-medium">
                Другие написания, через запятую
              </th>
              <th scope="col" className="w-12 px-2 py-2">
                <span className="sr-only">Удалить</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-3 py-4 text-muted">
                  Записей пока нет. Нажмите «Добавить строку».
                </td>
              </tr>
            ) : null}
            {rows.map((r, i) => (
              <tr key={r.key} className="border-t border-ink/30 align-top">
                <td className="px-2 py-1.5 font-mono tabular-nums">
                  <span className="inline-flex min-h-11 items-center">{i + 1}</span>
                </td>
                <td className="px-1 py-1.5">
                  <input
                    ref={i === rows.length - 1 ? lastRow : undefined}
                    name="value"
                    value={r.value}
                    onChange={(e) => update(r.key, { value: e.target.value })}
                    maxLength={200}
                    autoComplete="off"
                    aria-label={`Значение, строка ${i + 1}`}
                    className={CELL}
                  />
                </td>
                <td className="px-1 py-1.5">
                  <input
                    name="aliases"
                    value={r.aliases}
                    onChange={(e) => update(r.key, { aliases: e.target.value })}
                    autoComplete="off"
                    aria-label={`Написания, строка ${i + 1}`}
                    className={CELL}
                  />
                </td>
                <td className="px-1 py-1.5">
                  <button
                    type="button"
                    onClick={() => setRows((all) => all.filter((x) => x.key !== r.key))}
                    aria-label={`Удалить строку ${i + 1}`}
                    className="inline-flex size-11 items-center justify-center font-mono text-lg hover:text-accent-text"
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-6">
        <Button type="submit" disabled={pending}>
          {pending ? "Сохраняем…" : "Сохранить"}
        </Button>
        <button
          type="button"
          onClick={() => {
            setRows((r) => [...r, { key: next.current++, value: "", aliases: "" }]);
            setAdded(true);
          }}
          className="inline-flex min-h-11 items-center px-1 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text"
        >
          Добавить строку
        </button>
        <span className="font-mono text-sm text-muted">Записей: {rows.length}</span>
      </div>
    </form>
  );
}
