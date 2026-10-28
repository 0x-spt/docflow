import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { DestinationForm } from "@/components/app/DestinationForm";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { ConnectorInfo } from "@/lib/types";

import { loadMappingSchemas } from "../schemas";

export const metadata = { title: "Новое подключение — DocFlow" };

const BACK = "font-mono text-sm underline underline-offset-4 hover:text-accent-text";

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function NewIntegrationPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const me = await requireMe();
  if (me.user?.role !== "owner") redirect("/app/integrations?err=forbidden");
  const catalog = await apiFetchAuthed<ConnectorInfo[]>("/v1/connectors");
  const kind = one(sp.kind);

  if (!kind) {
    return (
      <div className="flex flex-col gap-6">
        <div>
          <Link href="/app/integrations" className={BACK}>
            ← Подключения
          </Link>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight">Что подключаем?</h1>
          <p className="mt-1 text-muted">Выберите систему, в которую нужно отправлять документы.</p>
        </div>
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {catalog.map((c) => (
            <li key={c.kind}>
              <Link
                href={`/app/integrations/new?kind=${encodeURIComponent(c.kind)}`}
                className="flex min-h-24 flex-col justify-between gap-3 border-2 border-ink bg-card p-4 shadow-hard hover:bg-paper active:translate-x-1.5 active:translate-y-1.5 active:shadow-none"
              >
                <span className="text-2xl font-semibold tracking-tight">{c.label}</span>
                <span className="font-mono text-sm text-muted">
                  {c.secret_fields.length > 0 ? "нужен ключ доступа" : "без ключей"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const connector = catalog.find((c) => c.kind === kind);
  if (!connector) notFound();
  const schemas = await loadMappingSchemas();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/app/integrations/new" className={BACK}>
          ← Другая система
        </Link>
        <h1 className="mt-2 text-4xl font-semibold tracking-tight">Подключение: {connector.label}</h1>
      </div>
      <DestinationForm connector={connector} schemas={schemas} />
    </div>
  );
}
