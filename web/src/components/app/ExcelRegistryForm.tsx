"use client";

import { useState, type FormEvent } from "react";

import { Button, Field } from "@/components/ui";

type Schema = { slug: string; title: string };

/** «Реестр в Excel»: файл скачивается без ухода со страницы, отказ API (422: период, лимит) — текстом под формой. */
export function ExcelRegistryForm({
  schemas,
  monthStart,
  today,
}: {
  schemas: Schema[];
  monthStart: string;
  today: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const query = new URLSearchParams(new FormData(e.currentTarget) as unknown as Record<string, string>);
    // период — сутки в поясе пользователя, а не сервера (без JS API возьмёт Europe/Moscow)
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz) query.set("tz", tz);
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(`/app/files/excel?${query}`);
      if (res.redirected && new URL(res.url).pathname.startsWith("/auth/")) {
        window.location.href = "/auth/expired";
        return;
      }
      if (!res.ok) {
        const text = (await res.text()).trim();
        setError(text && text.length < 300 ? text : "Не удалось сформировать реестр. Попробуйте ещё раз.");
        return;
      }
      const name = /filename="?([^";]+)"?/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "registry.xlsx";
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError("Не удалось сформировать реестр: нет связи с сервисом.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      method="get"
      action="/app/files/excel"
      onSubmit={onSubmit}
      className="mt-4 grid items-end gap-4 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr_auto]"
    >
      <Field as="select" label="Тип документа" name="schema" required defaultValue={schemas[0].slug}>
        {schemas.map((s) => (
          <option key={s.slug} value={s.slug}>
            {s.title}
          </option>
        ))}
      </Field>
      <Field label="С" name="from" type="date" required defaultValue={monthStart} />
      <Field label="По" name="to" type="date" required defaultValue={today} />
      <Field as="select" label="Статус документов" name="status" defaultValue="ok">
        <option value="ok">Готовые</option>
        <option value="needs_review">На проверке</option>
        <option value="all">Все</option>
      </Field>
      <Button type="submit" disabled={busy} aria-busy={busy}>
        {busy ? "Готовим…" : "Скачать реестр"}
      </Button>
      <div role="alert" className="sm:col-span-2 lg:col-span-5">
        {error ? <p className="font-medium text-accent-text">{error}</p> : null}
      </div>
    </form>
  );
}
