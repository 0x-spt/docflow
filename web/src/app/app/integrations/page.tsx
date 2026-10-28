import Link from "next/link";
import { unstable_rethrow } from "next/navigation";

import { TestConnection } from "@/components/app/TestConnection";
import { Button } from "@/components/ui";
import { plural } from "@/lib/format";
import { isUuid } from "@/lib/ids";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { ConnectorInfo, Destination, ExportsSummary } from "@/lib/types";

import { deleteDestination, setDestinationActive } from "./actions";

export const metadata = { title: "Подключения — DocFlow" };

const LINK =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text";

const ERRORS: Record<string, string> = {
  forbidden: "Подключениями управляет владелец аккаунта.",
  failed: "Не удалось выполнить действие. Проверьте соединение и попробуйте ещё раз.",
};

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function IntegrationsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const errCode = one(sp.err);
  const errorText = errCode && Object.hasOwn(ERRORS, errCode) ? ERRORS[errCode] : null;
  const confirmRaw = one(sp.confirm) ?? "";
  const confirmId = isUuid(confirmRaw) ? confirmRaw : null;
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";
  const [destinations, catalog, summary] = await Promise.all([
    apiFetchAuthed<Destination[]>("/v1/destinations"),
    apiFetchAuthed<ConnectorInfo[]>("/v1/connectors"),
    // счётчики ошибок — подсказка, не главное: без них страница всё равно нужна
    apiFetchAuthed<ExportsSummary>("/v1/exports/summary").catch((e) => {
      unstable_rethrow(e);
      console.error("не удалось получить сводку выгрузок", e);
      return null;
    }),
  ]);
  const labels = new Map(catalog.map((c) => [c.kind, c.label]));
  const failedBy = new Map((summary?.items ?? []).map((s) => [s.destination_id, s.failed]));

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-semibold tracking-tight">Подключения</h1>
          <p className="mt-1 max-w-3xl text-muted">
            Куда DocFlow отправляет готовые документы: таблицы, CRM, 1С, Telegram и почта.
          </p>
        </div>
        {isOwner ? <Button href="/app/integrations/new">Добавить подключение</Button> : null}
      </div>

      {!isOwner ? (
        <p className="border-2 border-ink bg-card p-4">Подключениями управляет владелец аккаунта.</p>
      ) : null}
      {errorText ? (
        <div role="alert" className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text">
          {errorText}
        </div>
      ) : null}

      {destinations.length === 0 ? (
        <div className="border-2 border-ink bg-card p-6">
          <p className="text-lg font-medium">Подключений пока нет.</p>
          <p className="mt-1 text-muted">
            {isOwner
              ? "Добавьте первое, чтобы документы уходили в вашу систему сами."
              : "Владелец аккаунта может добавить их."}
          </p>
        </div>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {destinations.map((d) => {
            const system = labels.get(d.kind) ?? d.kind;
            const confirming = confirmId === d.id;
            const failed = failedBy.get(d.id) ?? 0;
            return (
              <li key={d.id} className="flex flex-col gap-4 border-2 border-ink bg-card p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-mono text-sm text-muted">{system}</p>
                    <h2 className="break-words text-2xl font-semibold tracking-tight">{d.name}</h2>
                  </div>
                  <span className="inline-flex items-center gap-2 font-mono text-sm font-medium">
                    <span
                      aria-hidden="true"
                      className={`inline-block size-3 border-2 border-ink ${d.active ? "bg-ink" : "bg-card"}`}
                    />
                    {d.active ? "Включено" : "Отключено"}
                  </span>
                </div>
                {failed > 0 ? (
                  <Link
                    href={`/app/exports?status=failed&destination=${d.id}`}
                    className="inline-flex min-h-11 w-fit items-center gap-2 border-2 border-accent-text bg-paper px-3 font-mono text-sm font-medium text-accent-text hover:bg-card"
                    aria-label={`${failed} ${plural(failed, ["ошибка", "ошибки", "ошибок"])} выгрузки: ${d.name}`}
                  >
                    <span aria-hidden="true" className="inline-block size-3 border-2 border-ink bg-accent-text" />
                    {failed} {plural(failed, ["ошибка", "ошибки", "ошибок"])}
                  </Link>
                ) : null}
                {isOwner ? (
                  <>
                    <TestConnection id={d.id} />
                    <div className="flex flex-wrap items-center gap-x-2 border-t-2 border-ink pt-2">
                      <Link href={`/app/integrations/${d.id}`} className={LINK} aria-label={`Изменить: ${d.name}`}>
                        Изменить
                      </Link>
                      <form action={setDestinationActive.bind(null, d.id, !d.active, "list")}>
                        <button
                          type="submit"
                          className={LINK}
                          aria-label={`${d.active ? "Отключить" : "Включить"}: ${d.name}`}
                        >
                          {d.active ? "Отключить" : "Включить"}
                        </button>
                      </form>
                      {confirming ? (
                        <span className="flex flex-wrap items-center gap-2">
                          <form action={deleteDestination.bind(null, d.id)}>
                            <button type="submit" className={`${LINK} text-accent-text`}>
                              Точно удалить?
                            </button>
                          </form>
                          <Link href="/app/integrations" className={LINK}>
                            Отмена
                          </Link>
                        </span>
                      ) : (
                        <Link
                          href={`/app/integrations?confirm=${d.id}`}
                          className={LINK}
                          aria-label={`Удалить: ${d.name}`}
                        >
                          Удалить
                        </Link>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="border-t-2 border-ink pt-2">
                    <Link href={`/app/integrations/${d.id}`} className={LINK} aria-label={`Подробнее: ${d.name}`}>
                      Подробнее
                    </Link>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
