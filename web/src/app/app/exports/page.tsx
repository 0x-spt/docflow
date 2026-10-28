import Link from "next/link";

import { RetryExportButton } from "@/components/app/RetryExportButton";
import { formatDateTime } from "@/lib/format";
import { isUuid } from "@/lib/ids";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { ConnectorInfo, Destination, ExportItem, ExportLogItem } from "@/lib/types";

export const metadata = { title: "Ошибки выгрузки — DocFlow" };

const LIMIT = 200;

const PURPOSE: Record<ExportItem["purpose"], string> = {
  export: "Выгрузка",
  notify_review: "Уведомление о проверке",
  notify_new: "Уведомление о новом",
};

const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** Неудавшиеся выгрузки и уведомления: какой документ, куда, почему; владелец нажимает «Повторить». */
export default async function ExportsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const rawDest = one(sp.destination) ?? "";
  const destinationId = isUuid(rawDest) ? rawDest : null;

  const query = new URLSearchParams({ status: "failed", limit: String(LIMIT) });
  if (destinationId) query.set("destination_id", destinationId);
  const [me, log, destinations, catalog] = await Promise.all([
    requireMe(),
    apiFetchAuthed<{ items: ExportLogItem[] }>(`/v1/exports?${query}`),
    apiFetchAuthed<Destination[]>("/v1/destinations"),
    apiFetchAuthed<ConnectorInfo[]>("/v1/connectors"),
  ]);
  const canRetry = me.user?.role === "owner";
  const labels = new Map(catalog.map((c) => [c.kind, c.label]));
  const destination = destinationId ? destinations.find((d) => d.id === destinationId) : undefined;
  const items = log.items;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/app/integrations" className="font-mono text-sm underline underline-offset-4 hover:text-accent-text">
          ← Подключения
        </Link>
        <h1 className="mt-2 text-4xl font-semibold tracking-tight">Ошибки выгрузки</h1>
        <p className="mt-1 max-w-3xl text-muted">
          {destination ? (
            <>
              Подключение «{destination.name}». <Link href="/app/exports?status=failed" className={LINK}>Все подключения</Link>
            </>
          ) : (
            "Документы, которые не ушли в системы после всех повторов. Исправьте причину и нажмите «Повторить»."
          )}
        </p>
      </div>

      {items.length === 0 ? (
        <div className="border-2 border-ink bg-card p-6">
          <p className="text-lg font-medium">Ошибок выгрузки нет.</p>
          <p className="mt-1 text-muted">
            <Link href="/app" className={LINK}>
              К очереди документов
            </Link>
          </p>
        </div>
      ) : (
        <div className="relative overflow-x-auto border-2 border-ink bg-card">
          <table className="w-full min-w-[44rem] border-collapse text-left text-sm">
            <thead className="bg-ink text-paper">
              <tr>
                {["Документ", "Куда", "Ошибка", "Когда", ""].map((h, i) => (
                  <th key={i} scope="col" className="px-3 py-2 font-mono font-medium">
                    {h || <span className="sr-only">Действия</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((e) => {
                const name = e.destination_name ?? "Подключение удалено";
                return (
                  <tr key={e.id} className="border-t-2 border-ink align-top">
                    <td className="px-3 py-1.5">
                      <Link
                        href={`/app/documents/${e.document_id}`}
                        className={`inline-flex min-h-11 items-center font-mono font-medium ${LINK}`}
                        aria-label={`Документ ${e.document_id}`}
                      >
                        {e.document_id.slice(0, 8)}
                      </Link>
                      <div className="text-xs text-muted">{PURPOSE[e.purpose] ?? e.purpose}</div>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="break-words font-medium">{name}</div>
                      {e.kind ? <div className="font-mono text-xs text-muted">{labels.get(e.kind) ?? e.kind}</div> : null}
                    </td>
                    <td className="max-w-[22rem] px-3 py-2.5">
                      <p className="break-words font-medium text-accent-text">{e.error ?? "—"}</p>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">{formatDateTime(e.updated_at)}</td>
                    <td className="px-3 py-1.5">
                      {canRetry ? (
                        <RetryExportButton
                          docId={e.document_id}
                          exportId={e.id}
                          label={`${name}, ${PURPOSE[e.purpose] ?? e.purpose}`}
                        />
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {items.length === LIMIT ? (
        <p className="text-muted">Показаны последние {LIMIT}. Повторите или исправьте их, чтобы увидеть остальные.</p>
      ) : null}
      {!canRetry && items.length > 0 ? (
        <p className="text-muted">Повторять выгрузки может владелец аккаунта.</p>
      ) : null}
    </div>
  );
}
