"use client";

import { useState, useTransition } from "react";

import { resetSchema } from "@/app/app/fields/actions";
import { Button } from "@/components/ui";

const CONFIRM =
  "Вернуть стандартные поля? Новые документы пойдут по стандартной схеме, обработанные не изменятся. Названия удалённых полей сотрутся.";

/** Откат встроенного типа, перекрытого компанией, к стандартным полям (новая версия). */
export function ResetSchemaButton({ slug, version }: { slug: string; version: number }) {
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function onClick() {
    if (!window.confirm(CONFIRM)) return;
    setError(null);
    start(async () => {
      const res = await resetSchema(slug, version);
      if (res?.error) setError(res.error);
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <Button variant="ghost" onClick={onClick} disabled={pending}>
        Вернуть стандартные поля
      </Button>
      {error ? (
        <p role="alert" className="text-sm font-medium text-accent-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
