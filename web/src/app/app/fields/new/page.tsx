import { SchemaEditor } from "@/components/app/SchemaEditor";
import { AUTO_SLUG } from "@/lib/doc-type";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { Dictionary, SchemaListItem } from "@/lib/types";

export const metadata = { title: "Новый тип документа — DocFlow" };

export default async function NewSchemaPage() {
  const me = await requireMe();
  if (me.user?.role !== "owner") {
    return <p className="text-lg font-medium">Поля меняет владелец аккаунта.</p>;
  }
  const [schemas, dictionaries] = await Promise.all([
    apiFetchAuthed<SchemaListItem[]>("/v1/schemas"),
    apiFetchAuthed<Dictionary[]>("/v1/dictionaries"),
  ]);

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Новый тип документа</h1>
        <p className="mt-1 max-w-3xl text-muted">
          Служебное имя берётся из названия и после сохранения не меняется. Сохранение создаёт версию 1.
        </p>
      </div>
      <SchemaEditor
        initial={null}
        labels={{}}
        dictionaries={dictionaries.map((d) => ({ name: d.name, title: d.title }))}
        takenSlugs={[...schemas.map((s) => s.slug), AUTO_SLUG]}
      />
    </div>
  );
}
