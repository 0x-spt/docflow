"use client";

import { useId, useState, useTransition } from "react";

import { retypeDocument } from "@/app/app/documents/[id]/actions";
import type { SchemaListItem } from "@/lib/types";

/** Документ, у которого не удалось определить тип: выбрать тип самому и распознать заново. */
export function RetypeForm({ id, schemas }: { id: string; schemas: SchemaListItem[] }) {
  const [schema, setSchema] = useState(schemas[0]?.slug ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const selectId = useId();

  if (schemas.length === 0) return null;
  return (
    <form
      aria-busy={pending}
      onSubmit={(e) => {
        e.preventDefault();
        if (pending) return;
        setError(null);
        start(async () => {
          const res = await retypeDocument(id, schema);
          if (res.error) setError(res.error);
        });
      }}
      className="flex flex-col gap-3 border-2 border-ink bg-card p-4"
    >
      <label htmlFor={selectId} className="font-mono text-sm font-medium">
        Тип документа
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <select
          id={selectId}
          value={schema}
          onChange={(e) => setSchema(e.target.value)}
          className="min-h-11 min-w-0 border-2 border-ink bg-card px-3 text-base"
        >
          {schemas.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.title}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={pending}
          className="inline-flex min-h-11 items-center border-2 border-ink bg-card px-5 font-semibold shadow-hard active:translate-x-1.5 active:translate-y-1.5 active:shadow-none disabled:opacity-50"
        >
          {pending ? "Ставим в очередь…" : "Распознать заново"}
        </button>
      </div>
      {error ? (
        <p role="alert" className="font-medium text-accent-text">
          {error}
        </p>
      ) : null}
    </form>
  );
}
