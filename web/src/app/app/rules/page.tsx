import Link from "next/link";
import { unstable_rethrow } from "next/navigation";

import { RulesEditor, type RuleSchema } from "@/components/app/RulesEditor";
import { fieldLabel } from "@/lib/format";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type {
  ConnectorInfo,
  Destination,
  Dictionary,
  DocSchema,
  RoutingRules,
  SchemaListItem,
} from "@/lib/types";

export const metadata = { title: "Правила — DocFlow" };

const MAX_SCHEMAS = 30;
const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";

/** Схемы тенанта с плоскими полями (таблицы в условиях не участвуют). Сбой одной схемы её пропускает. */
async function loadSchemas(): Promise<RuleSchema[]> {
  let list: SchemaListItem[];
  try {
    list = await apiFetchAuthed<SchemaListItem[]>("/v1/schemas");
  } catch (e) {
    unstable_rethrow(e);
    return [];
  }
  const details = await Promise.all(
    list.slice(0, MAX_SCHEMAS).map(async (s) => {
      try {
        return await apiFetchAuthed<DocSchema>(`/v1/schemas/${encodeURIComponent(s.slug)}?version=${s.version}`);
      } catch (e) {
        unstable_rethrow(e);
        return null;
      }
    }),
  );
  return details
    .filter((d): d is DocSchema => d !== null)
    .map((d) => ({
      slug: d.slug,
      title: d.title,
      fields: d.fields.filter((f) => f.type !== "table").map((f) => ({ name: f.name, label: fieldLabel(f) })),
    }));
}

export default async function RulesPage() {
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";
  const [rules, destinations, catalog, dictionaries, schemas] = await Promise.all([
    apiFetchAuthed<RoutingRules>("/v1/routing-rules"),
    apiFetchAuthed<Destination[]>("/v1/destinations"),
    apiFetchAuthed<ConnectorInfo[]>("/v1/connectors"),
    apiFetchAuthed<Dictionary[]>("/v1/dictionaries"),
    loadSchemas(),
  ]);
  const caps = new Map(catalog.map((c) => [c.kind, c]));
  const targets = destinations.map((d) => ({
    id: d.id,
    name: d.name,
    system: caps.get(d.kind)?.label ?? d.kind,
    canExport: caps.get(d.kind)?.can_export ?? false,
    canNotify: caps.get(d.kind)?.can_notify ?? false,
    active: d.active,
  }));

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Правила</h1>
        <p className="mt-1 max-w-3xl text-muted">
          «Если документ такой — отправить туда». Подходят все правила, у которых выполнены условия; без правил
          документы никуда не выгружаются.
        </p>
      </div>
      {!isOwner ? (
        <p className="border-2 border-ink bg-card p-4">Правила меняет владелец аккаунта — вы можете только смотреть.</p>
      ) : null}
      {targets.length === 0 ? (
        <div className="border-2 border-ink bg-card p-4">
          <p className="font-medium">Сначала добавьте подключение: правилу некуда отправлять документы.</p>
          <p className="mt-1">
            <Link href="/app/integrations" className={LINK}>
              Перейти к подключениям
            </Link>
          </p>
        </div>
      ) : null}
      <RulesEditor
        initial={rules.rules}
        schemas={schemas}
        targets={targets}
        dictionaries={dictionaries.map((d) => ({ name: d.name, title: d.title }))}
        readOnly={!isOwner}
      />
    </div>
  );
}
