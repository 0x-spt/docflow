import { fieldLabel, valueToDisplay } from "@/lib/format";
import type { FieldNotes } from "@/lib/review-form";
import type { DocSchema, SchemaColumn } from "@/lib/types";

/** Данные документа только для просмотра (статусы кроме needs_review): поля по схеме, таблицы — таблицами. */
export function DocumentData({
  schema,
  data,
  notes,
}: {
  schema: DocSchema;
  data: Record<string, unknown>;
  notes: FieldNotes;
}) {
  const scalars = schema.fields.filter((f) => f.type !== "table");
  const tables = schema.fields.filter((f) => f.type === "table");
  const marked = (path: string) => (notes[path]?.problems.length ? "text-accent-text font-medium" : "");

  return (
    <div className="flex flex-col gap-6">
      <dl className="border-2 border-ink bg-card">
        {scalars.map((f) => (
          <div key={f.name} className="grid gap-x-4 border-b border-ink/30 px-4 py-2 last:border-b-0 sm:grid-cols-[14rem_1fr]">
            <dt className="font-mono text-sm text-muted">{fieldLabel(f)}</dt>
            <dd className={`break-words ${f.type === "string" ? "" : "font-mono tabular-nums"} ${marked(f.name)}`}>
              {valueToDisplay(f as SchemaColumn, data[f.name])}
            </dd>
          </div>
        ))}
      </dl>
      {tables.map((f) => {
        const rows = Array.isArray(data[f.name]) ? (data[f.name] as Record<string, unknown>[]) : [];
        const columns = f.columns ?? [];
        return (
          <section key={f.name} aria-label={fieldLabel(f)} className="flex flex-col gap-2">
            <h3 className="font-mono text-sm font-medium">{fieldLabel(f)}</h3>
            <div className="relative overflow-x-auto border-2 border-ink bg-card">
              <table className="w-full min-w-[32rem] border-collapse text-left text-sm">
                <thead className="bg-paper">
                  <tr className="border-b-2 border-ink">
                    <th scope="col" className="w-10 px-3 py-2 font-mono font-medium">
                      №
                    </th>
                    {columns.map((c) => (
                      <th key={c.name} scope="col" className="px-3 py-2 font-mono font-medium">
                        {fieldLabel(c)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={columns.length + 1} className="px-3 py-2 text-muted">
                        Строк нет
                      </td>
                    </tr>
                  ) : (
                    rows.map((row, i) => (
                      <tr key={i} className="border-t border-ink/30">
                        <td className="px-3 py-2 font-mono tabular-nums">{i + 1}</td>
                        {columns.map((c) => (
                          <td
                            key={c.name}
                            className={`px-3 py-2 ${c.type === "string" ? "" : "font-mono tabular-nums"} ${marked(`${f.name}[${i}].${c.name}`)}`}
                          >
                            {valueToDisplay(c, row?.[c.name])}
                          </td>
                        ))}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}
