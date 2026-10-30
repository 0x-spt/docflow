import { BotConnectForm } from "@/components/app/BotConnectForm";
import { BotPanel } from "@/components/app/BotPanel";
import { AUTO_SLUG } from "@/lib/doc-type";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { BotInfo, SchemaListItem } from "@/lib/types";

export const metadata = { title: "Telegram-бот — DocFlow" };

export default async function BotPage() {
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";
  const [{ bot }, schemas] = isOwner
    ? await Promise.all([
        apiFetchAuthed<{ bot: BotInfo | null }>("/v1/bot"),
        apiFetchAuthed<SchemaListItem[]>("/v1/schemas"),
      ])
    : [{ bot: null }, [] as SchemaListItem[]];
  const choices = schemas
    .filter((s) => s.slug !== AUTO_SLUG)
    .map((s) => ({ slug: s.slug, title: s.title }));

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Telegram-бот</h1>
        <p className="mt-1 max-w-3xl text-muted">
          Свой бот компании: сотрудники присылают в него фото и PDF, а результат
          приходит обратно в чат.
        </p>
      </div>

      {!isOwner ? (
        <p className="border-2 border-ink bg-card p-4">
          Бота подключает владелец аккаунта.
        </p>
      ) : bot ? (
        <BotPanel bot={bot} schemas={choices} />
      ) : (
        <section
          aria-labelledby="bot-connect"
          className="flex flex-col gap-4 border-2 border-ink bg-card p-6"
        >
          <h2
            id="bot-connect"
            className="text-2xl font-semibold tracking-tight"
          >
            Подключить бота
          </h2>
          <ol className="ml-5 flex max-w-2xl list-decimal flex-col gap-1">
            <li>Откройте в Telegram @BotFather и отправьте команду /newbot.</li>
            <li>Придумайте имя и адрес бота, которые он попросит.</li>
            <li>Скопируйте токен из ответа и вставьте его ниже.</li>
          </ol>
          <BotConnectForm />
        </section>
      )}
    </div>
  );
}
