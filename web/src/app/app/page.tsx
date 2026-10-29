import Link from "next/link";
import { unstable_rethrow } from "next/navigation";

import { ExcelRegistryForm } from "@/components/app/ExcelRegistryForm";
import { Button, Stat, StatusBadge } from "@/components/ui";
import { formatDateTime, summaryValue } from "@/lib/format";
import { apiFetchAuthed } from "@/lib/session";
import type { DocStatus, DocumentList, ExportsSummary, SchemaListItem, Stats } from "@/lib/types";

export const metadata = { title: "Очередь — DocFlow" };

const PAGE_SIZE = 50;
const STATS_DAYS = 30;

type Filter = { status: DocStatus | null; label: string; count: (s: Stats) => number };

const FILTERS: Filter[] = [
  { status: null, label: "Все", count: (s) => s.total },
  { status: "needs_review", label: "На проверке", count: (s) => s.by_status.needs_review },
  { status: "ok", label: "Готово", count: (s) => s.by_status.ok },
  { status: "failed", label: "Ошибки", count: (s) => s.by_status.failed },
  { status: "processing", label: "В обработке", count: (s) => s.by_status.processing },
  { status: "queued", label: "В очереди", count: (s) => s.by_status.queued },
];

const STATUSES = new Set<string>(FILTERS.flatMap((f) => (f.status ? [f.status] : [])));

const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

function queueHref(status: DocStatus | null, before?: string): string {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  if (before) q.set("before", before);
  const s = q.toString();
  return s ? `/app?${s}` : "/app";
}

function percent(share: number | null): string {
  return share === null ? "—" : `${Math.round(share * 100)}%`;
}

export default async function QueuePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const rawStatus = one(sp.status);
  const status = rawStatus && STATUSES.has(rawStatus) ? (rawStatus as DocStatus) : null;
  const rawBefore = one(sp.before);
  const before = rawBefore && !Number.isNaN(Date.parse(rawBefore)) ? rawBefore : undefined;

  const listQuery = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (status) listQuery.set("status", status);
  if (before) listQuery.set("before", before);

  const [stats, list, next, schemas, exportsSummary] = await Promise.all([
    apiFetchAuthed<Stats>(`/v1/stats?days=${STATS_DAYS}`),
    apiFetchAuthed<DocumentList>(`/v1/documents?${listQuery}`),
    apiFetchAuthed<DocumentList>("/v1/documents?status=needs_review&limit=1"),
    apiFetchAuthed<SchemaListItem[]>("/v1/schemas").catch((e) => {
      unstable_rethrow(e);
      return [] as SchemaListItem[];
    }),
    apiFetchAuthed<ExportsSummary>("/v1/exports/summary").catch((e) => {
      unstable_rethrow(e);
      console.error("не удалось получить сводку выгрузок", e);
      return null;
    }),
  ]);
  const failedExports = exportsSummary?.failed ?? 0;
  const today = new Date();
  const monthStart = new Date(Date.UTC(today.getFullYear(), today.getMonth(), 1)).toISOString().slice(0, 10);
  const todayIso = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())).toISOString().slice(0, 10);
  const nextReview = next.items[0];
  const by = stats.by_status;
  const processed = by.ok + by.needs_review + by.failed;
  // «В очереди» показываем, только когда там что-то есть или этот фильтр уже выбран
  const filters = FILTERS.filter((f) => f.status !== "queued" || by.queued > 0 || status === "queued");

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-semibold tracking-tight">Очередь</h1>
          <p className="mt-1 text-muted">Цифры и счётчики — за последние {STATS_DAYS} дней.</p>
        </div>
        {nextReview ? (
          <Button href={`/app/documents/${nextReview.id}`} variant="accent">
            Открыть следующий на проверку
          </Button>
        ) : null}
      </div>

      {failedExports > 0 ? (
        <Link
          href="/app/exports?status=failed"
          className="inline-flex min-h-11 w-fit items-center gap-2 border-2 border-accent-text bg-paper px-4 font-medium text-accent-text hover:bg-card"
        >
          <span aria-hidden="true" className="inline-block size-3 border-2 border-ink bg-accent-text" />
          Ошибки выгрузки: <span className="font-mono tabular-nums">{failedExports}</span>
        </Link>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat value={processed} label="Обработано" />
        <Stat value={percent(stats.auto_share)} label="Без участия человека" />
        <Stat value={by.needs_review} label="На проверке" tone={by.needs_review > 0 ? "alert" : "default"} />
        <Stat value={by.failed} label="Ошибки" tone={by.failed > 0 ? "alert" : "default"} />
      </div>

      {schemas.length > 0 ? (
        <section aria-labelledby="excel-title" className="border-2 border-ink bg-card p-4">
          <h2 id="excel-title" className="text-2xl font-semibold tracking-tight">
            Реестр в Excel
          </h2>
          <p className="mt-1 text-muted">Документы одного типа за период — таблицей для бухгалтерии. Период считается по дате загрузки в вашем часовом поясе.</p>
          <ExcelRegistryForm schemas={schemas} monthStart={monthStart} today={todayIso} />
        </section>
      ) : null}

      <section aria-labelledby="docs-title" className="flex flex-col gap-4">
        <h2 id="docs-title" className="sr-only">
          Документы
        </h2>
        <nav aria-label="Фильтр по статусу">
          <ul className="flex flex-wrap border-2 border-ink bg-card">
            {filters.map((f) => {
              const current = f.status === status;
              return (
                <li key={f.label} className="border-r-2 border-ink">
                  <Link
                    href={queueHref(f.status)}
                    aria-current={current ? "page" : undefined}
                    className={`inline-flex min-h-11 items-center gap-2 px-4 font-medium ${
                      current ? "bg-ink text-paper" : "hover:bg-paper"
                    }`}
                  >
                    {f.label}
                    <span className="font-mono text-sm tabular-nums">{f.count(stats)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {list.items.length === 0 ? (
          <div className="border-2 border-ink bg-card p-6">
            <p className="text-lg font-medium">
              {before ? "Дальше документов нет." : status ? "С таким статусом документов нет." : "Документов пока нет."}
            </p>
            <p className="mt-1 text-muted">
              {status || before ? (
                <Link href="/app" className={LINK}>
                  Показать все документы
                </Link>
              ) : (
                <>
                  Загрузите первый документ на странице{" "}
                  <Link href="/app/upload" className={LINK}>
                    «Загрузить»
                  </Link>{" "}
                  или через API.
                </>
              )}
            </p>
          </div>
        ) : (
          <div className="relative overflow-x-auto border-2 border-ink bg-card">
            <table className="w-full min-w-[46rem] border-collapse text-left text-sm">
              <thead className="bg-ink text-paper">
                <tr>
                  {["Документ", "Данные", "Замечание", "Статус", "Загружен"].map((h) => (
                    <th key={h} scope="col" className="px-3 py-2 font-mono font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {list.items.map((d) => (
                  <tr key={d.id} className="border-t-2 border-ink align-top hover:bg-paper">
                    <td className="px-3 py-1.5">
                      <Link
                        href={`/app/documents/${d.id}`}
                        className={`inline-flex min-h-11 items-center font-mono text-base font-medium ${LINK}`}
                      >
                        {d.title}
                      </Link>
                      <div className="font-mono text-xs text-muted">{d.schema.slug}</div>
                    </td>
                    <td className="max-w-[22rem] px-3 py-2.5">
                      {d.summary.length ? (
                        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
                          {d.summary.map((s) => (
                            <div key={s.field} className="contents">
                              <dt className="text-xs leading-5 text-muted">{s.label}</dt>
                              <dd className="truncate" title={summaryValue(s.value)}>
                                {summaryValue(s.value)}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="max-w-[18rem] px-3 py-2.5">
                      {d.issue ? (
                        <span className="font-medium text-accent-text">{d.issue}</span>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5">
                      <StatusBadge status={d.status} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">
                      {formatDateTime(d.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {list.next_before || before ? (
          <div className="flex flex-wrap gap-4">
            {list.next_before ? (
              <Button href={queueHref(status, list.next_before)} variant="ghost">
                Показать ещё
              </Button>
            ) : null}
            {before ? (
              <Button href={queueHref(status)} variant="ghost">
                К новым документам
              </Button>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}
