"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { parseSenders } from "@/lib/channels";
import { apiFetchAuthed } from "@/lib/session";
import type { MailboxInfo, MailboxTestResult } from "@/lib/types";

export type MailResult = { error?: string; ok?: boolean };

/** Поля формы ящика. Пароль приходит только сюда, обратно не возвращается. */
export type MailboxInput = {
  host: string;
  port: string;
  username: string;
  password: string;
  folder: string;
  senders: string;
  defaultSchema: string;
};

const FAILED = "Сервис недоступен. Попробуйте ещё раз.";
const FORBIDDEN = "Почту подключает владелец аккаунта.";
const PAGE = "/app/mail";

/** 4xx — текст API (без пароля); прочее — общий текст, в лог только тип сбоя. */
function failure(e: unknown, what: string): { error: string } {
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

/** Создать ящик или изменить настройки; пустой пароль оставляет сохранённый. */
export async function saveMailbox(input: MailboxInput): Promise<MailResult> {
  const { senders, errors } = parseSenders(String(input.senders ?? ""));
  if (errors.length > 0)
    return { error: `Неверные адреса отправителей: ${errors.join(", ")}` };
  const port = Number(String(input.port ?? "").trim());
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    return { error: "Порт должен быть от 1 до 65535" };
  const password = String(input.password ?? "");
  try {
    await apiFetchAuthed<MailboxInfo>("/v1/mailbox", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: String(input.host ?? "").trim(),
        port,
        username: String(input.username ?? "").trim(),
        ...(password ? { password } : {}),
        folder: String(input.folder ?? "").trim() || "INBOX",
        allowed_senders: senders,
        default_schema: input.defaultSchema
          ? String(input.defaultSchema)
          : null,
      }),
    });
  } catch (e) {
    return failure(e, "ящик не сохранён");
  }
  revalidatePath(PAGE);
  return { ok: true };
}

export async function deleteMailbox(): Promise<MailResult> {
  try {
    await apiFetchAuthed<void>("/v1/mailbox", { method: "DELETE" });
  } catch (e) {
    return failure(e, "ящик не отключён");
  }
  revalidatePath(PAGE);
  return { ok: true };
}

/** Проверка входа в ящик: ответ API {ok, message} показывается как есть (сообщения без пароля). */
export async function testMailbox(): Promise<{ ok: boolean; message: string }> {
  try {
    const r = await apiFetchAuthed<MailboxTestResult>("/v1/mailbox/test", {
      method: "POST",
    });
    return { ok: r.ok, message: r.message };
  } catch (e) {
    return {
      ok: false,
      message: failure(e, "проверка ящика не выполнена").error,
    };
  }
}
