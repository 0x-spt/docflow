import { notFound } from "next/navigation";

import { SchemaEditor } from "@/components/app/SchemaEditor";
import { ApiError } from "@/lib/api";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { Dictionary, DocSchema, FieldLabels } from "@/lib/types";

export const metadata = { title: "Изменить поля — DocFlow" };

const SLUG_RE = /^[a-z0-9_-]{1,80}$/;

export default async function EditFieldsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!SLUG_RE.test(slug)) notFound();
  const me = await requireMe();
  if (me.user?.role !== "owner") {
    return <p className="text-lg font-medium">Поля меняет владелец аккаунта.</p>;
  }

  const path = `/v1/schemas/${encodeURIComponent(slug)}`;
  let schema: DocSchema;
  let labelSet: FieldLabels;
  let dictionaries: Dictionary[];
  try {
    // raw: подписи компании не должны попасть в саму схему
    [schema, labelSet, dictionaries] = await Promise.all([
      apiFetchAuthed<DocSchema>(`${path}?raw=true`),
      apiFetchAuthed<FieldLabels>(`${path}/labels`),
      apiFetchAuthed<Dictionary[]>("/v1/dictionaries"),
    ]);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const labels = Object.fromEntries(labelSet.rows.filter((r) => r.label).map((r) => [r.key, r.label]));

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Поля: {schema.title}</h1>
        <p className="mt-1 max-w-3xl text-muted">
          Сохранение создаёт новую версию типа. Обработанные документы остаются на своей версии.
        </p>
      </div>
      <SchemaEditor
        initial={schema}
        labels={labels}
        dictionaries={dictionaries.map((d) => ({ name: d.name, title: d.title }))}
        takenSlugs={[]}
      />
    </div>
  );
}
