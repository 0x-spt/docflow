import Link from "next/link";
import { notFound } from "next/navigation";

import { DestinationForm } from "@/components/app/DestinationForm";
import { TestConnection } from "@/components/app/TestConnection";
import { ApiError } from "@/lib/api";
import { isUuid } from "@/lib/ids";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { ConnectorInfo, Destination } from "@/lib/types";

import { setDestinationActive } from "../actions";
import { loadMappingSchemas } from "../schemas";

export const metadata = { title: "Подключение — DocFlow" };

const LINK =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text";

const ERRORS: Record<string, string> = {
  forbidden: "Подключениями управляет владелец аккаунта.",
  failed: "Не удалось выполнить действие. Проверьте соединение и попробуйте ещё раз.",
};

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function IntegrationPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const sp = await searchParams;
  const errCode = one(sp.err);
  const errorText = errCode && Object.hasOwn(ERRORS, errCode) ? ERRORS[errCode] : null;
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";

  let destination: Destination;
  try {
    destination = await apiFetchAuthed<Destination>(`/v1/destinations/${id}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const catalog = await apiFetchAuthed<ConnectorInfo[]>("/v1/connectors");
  const connector = catalog.find((c) => c.kind === destination.kind);
  const schemas = isOwner && connector ? await loadMappingSchemas() : [];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/app/integrations" className="font-mono text-sm underline underline-offset-4 hover:text-accent-text">
          ← Подключения
        </Link>
        <p className="mt-2 font-mono text-sm text-muted">{connector?.label ?? destination.kind}</p>
        <h1 className="break-words text-4xl font-semibold tracking-tight">{destination.name}</h1>
        <p className="mt-2 inline-flex items-center gap-2 font-mono text-sm font-medium">
          <span
            aria-hidden="true"
            className={`inline-block size-3 border-2 border-ink ${destination.active ? "bg-ink" : "bg-card"}`}
          />
          {destination.active ? "Включено" : "Отключено"}
        </p>
      </div>

      {one(sp.created) ? (
        <p role="status" className="border-2 border-ink bg-card p-4 font-medium">
          Подключение добавлено. Проверьте, что оно работает.
        </p>
      ) : null}
      {errorText ? (
        <div role="alert" className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text">
          {errorText}
        </div>
      ) : null}

      {!isOwner || !connector ? (
        <div className="flex flex-col gap-4">
          <p className="border-2 border-ink bg-card p-4">Подключениями управляет владелец аккаунта.</p>
          <dl className="grid max-w-2xl grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 border-2 border-ink bg-card p-4">
            {(connector?.config_fields ?? []).map((f) => (
              <div key={f.name} className="contents">
                <dt className="font-mono text-sm text-muted">{f.label}</dt>
                <dd className="break-words">{String(destination.config[f.name] ?? "—")}</dd>
              </div>
            ))}
            <dt className="font-mono text-sm text-muted">Секреты</dt>
            <dd>{destination.has_secrets ? "Сохранены" : "Не заданы"}</dd>
            <dt className="font-mono text-sm text-muted">Сопоставлено полей</dt>
            <dd>{Object.keys(destination.mapping).length}</dd>
          </dl>
        </div>
      ) : (
        <>
          <div className="flex max-w-2xl flex-col gap-3 border-2 border-ink bg-card p-4">
            <h2 className="text-2xl font-semibold tracking-tight">Состояние</h2>
            <TestConnection id={destination.id} />
            <form action={setDestinationActive.bind(null, destination.id, !destination.active, "item")}>
              <button type="submit" className={LINK}>
                {destination.active ? "Отключить подключение" : "Включить подключение"}
              </button>
            </form>
            <p className="text-sm text-muted">
              Отключённое подключение не получает документы, но настройки сохраняются.
            </p>
          </div>
          <DestinationForm
            connector={connector}
            destination={destination}
            schemas={schemas}
          />
        </>
      )}
    </div>
  );
}
