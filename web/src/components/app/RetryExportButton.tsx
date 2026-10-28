"use client";

import { useState, useTransition } from "react";

import { retryExport } from "@/app/app/documents/[id]/actions";

/** «Повторить» для неудавшейся выгрузки; отказ API (409 и др.) — текстом рядом. */
export function RetryExportButton({ docId, exportId, label }: { docId: string; exportId: string; label: string }) {
  const [message, setMessage] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        disabled={pending}
        aria-busy={pending}
        aria-label={`Повторить выгрузку: ${label}`}
        onClick={() => {
          setMessage(null);
          start(async () => {
            const res = await retryExport(docId, exportId);
            if (res.error) setMessage(res.error);
          });
        }}
        className="inline-flex min-h-11 items-center border-2 border-ink bg-card px-3 font-semibold shadow-hard active:translate-x-1.5 active:translate-y-1.5 active:shadow-none disabled:opacity-50"
      >
        {pending ? "Повторяем…" : "Повторить"}
      </button>
      <div role="status" aria-live="polite">
        {message ? <p className="max-w-[16rem] text-sm font-medium text-accent-text">{message}</p> : null}
      </div>
    </div>
  );
}
