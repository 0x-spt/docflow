"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { isUuid } from "@/lib/ids";
import { apiFetchAuthed } from "@/lib/session";
import type { BotInfo } from "@/lib/types";

export type BotResult = { error?: string; ok?: boolean };

const FAILED = "Сервис недоступен. Попробуйте ещё раз.";
const FORBIDDEN = "Бота подключает владелец аккаунта.";
const PAGE = "/app/bot";
const JSON_HEADERS = { "Content-Type": "application/json" };

/** 4xx — текст API (без токена); прочее — общий текст, в лог только тип сбоя: токен мог бы попасть в объект ошибки. */
function failure(e: unknown, what: string): BotResult {
  unstable_rethrow(e);
  if (e instanceof ApiError && e.status === 403) return { error: FORBIDDEN };
  if (e instanceof ApiError && e.status >= 400 && e.status < 500)
    return { error: e.detail };
  console.error(
    what,
    e instanceof ApiError ? `ответ ${e.status}` : (e as Error)?.name,
  );
  return { error: FAILED };
}

async function run(
  what: string,
  call: () => Promise<unknown>,
): Promise<BotResult> {
  try {
    await call();
  } catch (e) {
    return failure(e, what);
  }
  revalidatePath(PAGE);
  return { ok: true };
}

/** Подключить бота по токену. Токен уходит только в API и нигде не сохраняется на сайте. */
export async function connectBot(token: string): Promise<BotResult> {
  return run("бот не подключён", () =>
    apiFetchAuthed<BotInfo>("/v1/bot", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ token }),
    }),
  );
}

export async function disconnectBot(): Promise<BotResult> {
  return run("бот не отключён", () =>
    apiFetchAuthed<void>("/v1/bot", { method: "DELETE" }),
  );
}

export async function newInvite(): Promise<BotResult> {
  return run("ссылка не обновлена", () =>
    apiFetchAuthed<BotInfo>("/v1/bot/invite", { method: "POST" }),
  );
}

/** slug пустой — определять тип автоматически. */
export async function setBotDefaultSchema(slug: string): Promise<BotResult> {
  return run("тип по умолчанию не изменён", () =>
    apiFetchAuthed<BotInfo>("/v1/bot", {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ default_schema: slug === "" ? null : slug }),
    }),
  );
}

export async function setChatNotifications(
  chatId: string,
  allDocuments: boolean,
): Promise<BotResult> {
  if (!isUuid(chatId)) return { error: "Чат не найден." };
  return run("уведомления чата не изменены", () =>
    apiFetchAuthed<unknown>(`/v1/bot/chats/${chatId}`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ all_documents: allDocuments }),
    }),
  );
}

export async function unlinkChat(chatId: string): Promise<BotResult> {
  if (!isUuid(chatId)) return { error: "Чат не найден." };
  return run("чат не отвязан", () =>
    apiFetchAuthed<void>(`/v1/bot/chats/${chatId}`, { method: "DELETE" }),
  );
}
