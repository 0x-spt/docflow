import { UploadForm } from "@/components/app/UploadForm";
import { apiFetchAuthed } from "@/lib/session";
import type { SchemaListItem } from "@/lib/types";

export const metadata = { title: "Загрузить — DocFlow" };

export default async function UploadPage() {
  const schemas = await apiFetchAuthed<SchemaListItem[]>("/v1/schemas");
  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Загрузить документ</h1>
        <p className="mt-1 text-muted">
          Документ попадёт в очередь и обработается автоматически; сомнительные поля вы подтвердите вручную.
        </p>
      </div>
      {schemas.length === 0 ? (
        <p className="border-2 border-ink bg-card p-6">Нет доступных типов документов.</p>
      ) : (
        <UploadForm schemas={schemas} />
      )}
    </div>
  );
}
