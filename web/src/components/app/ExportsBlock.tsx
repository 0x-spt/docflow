import { RetryExportButton } from "@/components/app/RetryExportButton";
import { formatDateTime } from "@/lib/format";
import type { ConnectorInfo, ExportItem, ExportStatus } from "@/lib/types";

const PURPOSE: Record<ExportItem["purpose"], string> = {
  export: "Выгрузка",
  notify_review: "Уведомление о проверке",
  notify_new: "Уведомление о новом",
};

const STATUS: Record<ExportStatus, { label: string; square: string }> = {
  done: { label: "Отправлено", square: "bg-ink" },
  pending: { label: "В очереди", square: "bg-card" },
  failed: { label: "Ошибка", square: "bg-accent-text" },
  skipped: { label: "Пропущено", square: "bg-muted" },
};

/** «Выгрузки» документа: куда ушёл, с каким результатом; для failed — «Повторить» (владелец). */
export function ExportsBlock({
  docId,
  exports,
  catalog,
  canRetry,
}: {
  docId: string;
  exports: ExportItem[];
  catalog: ConnectorInfo[];
  canRetry: boolean;
}) {
  if (exports.length === 0) return null;
  const labels = new Map(catalog.map((c) => [c.kind, c.label]));
  return (
    <section aria-labelledby="exports-title" className="flex flex-col gap-3">
      <h2 id="exports-title" className="text-2xl font-semibold tracking-tight">
        Выгрузки <span className="font-mono text-lg font-medium text-muted">{exports.length}</span>
      </h2>
      <div className="relative overflow-x-auto border-2 border-ink bg-card">
        <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
          <thead className="bg-ink text-paper">
            <tr>
              {["Куда", "Что", "Статус", "ID в системе", "Обновлено", ""].map((h, i) => (
                <th key={i} scope="col" className="px-3 py-2 font-mono font-medium">
                  {h || <span className="sr-only">Действия</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {exports.map((e) => {
              const st = STATUS[e.status];
              const name = e.destination_name ?? "Подключение удалено";
              return (
                <tr key={e.id} className="border-t-2 border-ink align-top">
                  <td className="px-3 py-2.5">
                    <div className="break-words font-medium">{name}</div>
                    {e.kind ? <div className="font-mono text-xs text-muted">{labels.get(e.kind) ?? e.kind}</div> : null}
                  </td>
                  <td className="px-3 py-2.5">{PURPOSE[e.purpose] ?? e.purpose}</td>
                  <td className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-2 font-mono text-sm font-medium">
                      <span aria-hidden="true" className={`inline-block size-3 border-2 border-ink ${st.square}`} />
                      {st.label}
                    </span>
                    {e.error ? (
                      <p
                        className={`mt-1 max-w-[20rem] break-words ${
                          e.status === "skipped" ? "text-muted" : "font-medium text-accent-text"
                        }`}
                      >
                        {e.error}
                      </p>
                    ) : null}
                  </td>
                  <td className="px-3 py-2.5 font-mono break-all">{e.external_id ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">{formatDateTime(e.updated_at)}</td>
                  <td className="px-3 py-1.5">
                    {e.status === "failed" && canRetry ? (
                      <RetryExportButton docId={docId} exportId={e.id} label={`${name}, ${PURPOSE[e.purpose]}`} />
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
