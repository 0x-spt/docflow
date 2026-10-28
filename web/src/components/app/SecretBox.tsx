"use client";

import { useState } from "react";

/** Секрет, который показывается один раз (ключ API, секрет вебхука): рамка, значение, кнопка «Скопировать». */
export function SecretBox({ title, value, note }: { title: string; value: string; note: string }) {
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied("ok");
    } catch {
      setCopied("fail"); // нет доступа к буферу (http, запрет браузера): значение выделяется вручную
    }
  }

  return (
    <div className="flex flex-col gap-3 border-2 border-ink bg-card p-4 shadow-hard" role="status">
      <p className="font-medium">{title}</p>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
        <code
          className="min-w-0 flex-1 select-all break-all border-2 border-ink bg-paper px-3 py-2 font-mono text-sm"
          tabIndex={0}
        >
          {value}
        </code>
        <button
          type="button"
          onClick={copy}
          className="inline-flex min-h-11 items-center justify-center border-2 border-ink bg-ink px-5 font-semibold text-paper"
        >
          {copied === "ok" ? "Скопировано" : "Скопировать"}
        </button>
      </div>
      <p className="text-sm text-muted">
        {copied === "fail" ? "Не удалось скопировать автоматически — выделите значение и скопируйте. " : null}
        {note}
      </p>
    </div>
  );
}
