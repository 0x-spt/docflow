"use client";

import { useState, useTransition } from "react";

import {
  disconnectBot,
  newInvite,
  setBotDefaultSchema,
  setChatNotifications,
  unlinkChat,
  type BotResult,
} from "@/app/app/bot/actions";
import { SecretBox } from "@/components/app/SecretBox";
import { Button } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { BotInfo } from "@/lib/types";

const LINK_BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text disabled:opacity-50";
const ALERT = "break-words border-2 border-accent-text bg-paper p-4 font-medium text-accent-text";

/** Подключённый бот: ссылки-приглашения, тип по умолчанию, чаты, отключение. Токена здесь нет: API его не отдаёт. */
export function BotPanel({ bot, schemas }: { bot: BotInfo; schemas: { slug: string; title: string }[] }) {
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function act(fn: () => Promise<BotResult>) {
    if (pending) return;
    setError(null);
    start(async () => {
      const res = await fn();
      if (res.error) setError(res.error);
    });
  }

  return (
    <div className="flex flex-col gap-8" aria-busy={pending}>
      <div className="flex flex-wrap items-center justify-between gap-4 border-2 border-ink bg-card p-4">
        <p className="min-w-0 break-all font-mono text-xl font-medium">@{bot.username}</p>
        <Button
          variant="ghost"
          disabled={pending}
          onClick={() => {
            if (window.confirm("Отключить бота? Все привязанные чаты будут отвязаны.")) act(disconnectBot);
          }}
        >
          Отключить бота
        </Button>
      </div>

      {!bot.active ? (
        <p role="alert" className={ALERT}>
          Токен больше не действует. Отключите бота и подключите заново.
        </p>
      ) : null}
      {bot.last_error ? (
        <p role="alert" className={ALERT}>
          Последняя ошибка: {bot.last_error}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className={ALERT}>
          {error}
        </p>
      ) : null}

      <section aria-labelledby="bot-type" className="flex flex-col gap-2">
        <h2 id="bot-type" className="text-2xl font-semibold tracking-tight">
          Тип документов
        </h2>
        <label htmlFor="bot-default-schema" className="font-mono text-sm font-medium">
          Тип по умолчанию
        </label>
        <select
          id="bot-default-schema"
          value={bot.default_schema ?? ""}
          disabled={pending}
          onChange={(e) => act(() => setBotDefaultSchema(e.target.value))}
          className="min-h-11 w-full max-w-md border-2 border-ink bg-card px-3 py-2 text-base"
        >
          <option value="">Определять автоматически</option>
          {schemas.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.title}
            </option>
          ))}
        </select>
      </section>

      <section aria-labelledby="bot-links" className="flex flex-col gap-4">
        <h2 id="bot-links" className="text-2xl font-semibold tracking-tight">
          Ссылки для привязки
        </h2>
        <SecretBox
          title="Ссылка для личного чата"
          value={bot.invite_link}
          note="Отправьте сотруднику: он откроет бота и нажмёт «Старт»."
        />
        <SecretBox
          title="Ссылка для группы"
          value={bot.group_invite_link}
          note="Telegram предложит выбрать группу. Чтобы бот видел сообщения, сделайте его администратором группы или отключите privacy mode у @BotFather."
        />
        <div className="flex flex-col items-start gap-1">
          <Button
            variant="ghost"
            disabled={pending}
            onClick={() => {
              if (window.confirm("Выпустить новые ссылки? Старые перестанут работать.")) act(newInvite);
            }}
          >
            Новая ссылка
          </Button>
          <p className="max-w-xl text-sm text-muted">
            Старая ссылка перестанет работать, но уже привязанные чаты останутся привязанными.
          </p>
        </div>
      </section>

      <section aria-labelledby="bot-chats" className="flex flex-col gap-3">
        <h2 id="bot-chats" className="text-2xl font-semibold tracking-tight">
          Привязанные чаты
        </h2>
        {bot.chats.length === 0 ? (
          <p className="border-2 border-ink bg-card p-4 text-muted">Пока ни один чат не привязан.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] border-2 border-ink bg-card text-left">
              <thead>
                <tr className="border-b-2 border-ink font-mono text-sm">
                  <th scope="col" className="p-3">
                    Чат
                  </th>
                  <th scope="col" className="p-3">
                    Имя пользователя
                  </th>
                  <th scope="col" className="p-3">
                    Привязан
                  </th>
                  <th scope="col" className="p-3">
                    Уведомления
                  </th>
                  <th scope="col" className="p-3">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {bot.chats.map((c) => {
                  const name = c.title || (c.username ? `@${c.username}` : "Без названия");
                  return (
                    <tr key={c.id} className="border-b border-ink last:border-b-0">
                      <td className="break-words p-3 font-medium">{c.title || "Без названия"}</td>
                      <td className="break-all p-3 font-mono text-sm">{c.username ? `@${c.username}` : "—"}</td>
                      <td className="p-3 font-mono text-sm">{formatDateTime(c.linked_at)}</td>
                      <td className="p-3">
                        <label className="inline-flex min-h-11 items-center gap-2">
                          <input
                            type="checkbox"
                            role="switch"
                            checked={c.all_documents}
                            disabled={pending}
                            onChange={(e) => act(() => setChatNotifications(c.id, e.target.checked))}
                            className="size-5 accent-ink"
                          />
                          <span>
                            Уведомления обо всех документах
                            <span className="sr-only">: {name}</span>
                          </span>
                        </label>
                      </td>
                      <td className="p-3">
                        <button
                          type="button"
                          disabled={pending}
                          aria-label={`Отвязать: ${name}`}
                          className={LINK_BTN}
                          onClick={() => {
                            if (window.confirm(`Отвязать чат «${name}»?`)) act(() => unlinkChat(c.id));
                          }}
                        >
                          Отвязать
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
