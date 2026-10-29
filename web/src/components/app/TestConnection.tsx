"use client";

import { useState, useTransition } from "react";

import { testDestination } from "@/app/app/integrations/actions";
import { Button } from "@/components/ui";
import type { TestResult } from "@/lib/types";

/** «Проверить подключение»: результат (успех или причина сбоя по-русски) в блоке состояния под кнопкой. */
export function TestConnection({ id, compact = false }: { id: string; compact?: boolean }) {
  const [result, setResult] = useState<TestResult | null>(null);
  const [pending, start] = useTransition();

  function run() {
    setResult(null);
    start(async () => setResult(await testDestination(id)));
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button variant="ghost" onClick={run} disabled={pending} aria-busy={pending} className={compact ? "px-3" : ""}>
          {pending ? "Проверяем…" : "Проверить подключение"}
        </Button>
      </div>
      <div role="status" aria-live="polite">
        {result ? (
          <p
            className={`border-2 p-3 text-sm font-medium break-words ${
              result.ok ? "border-ink bg-card" : "border-accent-text bg-paper text-accent-text"
            }`}
          >
            <span className="font-mono">{result.ok ? "Работает. " : "Не работает. "}</span>
            {result.message}
          </p>
        ) : null}
      </div>
    </div>
  );
}
