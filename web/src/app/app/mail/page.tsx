import { MailboxForm } from "@/components/app/MailboxForm";
import { AUTO_SLUG } from "@/lib/doc-type";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { MailboxInfo, SchemaListItem } from "@/lib/types";

export const metadata = { title: "Почта — DocFlow" };

export default async function MailPage() {
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";
  const [{ mailbox }, schemas] = isOwner
    ? await Promise.all([
        apiFetchAuthed<{ mailbox: MailboxInfo | null }>("/v1/mailbox"),
        apiFetchAuthed<SchemaListItem[]>("/v1/schemas"),
      ])
    : [{ mailbox: null }, [] as SchemaListItem[]];
  const choices = schemas
    .filter((s) => s.slug !== AUTO_SLUG)
    .map((s) => ({ slug: s.slug, title: s.title }));

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Почта</h1>
        <p className="mt-1 max-w-3xl text-muted">
          DocFlow забирает вложения из писем в вашем ящике и распознаёт их как
          обычные документы.
        </p>
      </div>

      {!isOwner ? (
        <p className="border-2 border-ink bg-card p-4">
          Почту подключает владелец аккаунта.
        </p>
      ) : (
        // key: при подключении и отключении форма начинается заново
        <MailboxForm
          key={mailbox ? "box" : "new"}
          mailbox={mailbox}
          schemas={choices}
        />
      )}
    </div>
  );
}
