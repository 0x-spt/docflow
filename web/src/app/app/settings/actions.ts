"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { apiFetchAuthed } from "@/lib/session";
import type { ApiKeyCreated, WebhookCreated, WebhookEvent } from "@/lib/types";

export type KeyState = { error?: string; key?: string; prefix?: string };
export type WebhookState = { error?: string; secret?: string; url?: string };

const EVENTS: WebhookEvent[] = ["document.completed", "document.failed", "document.reviewed"];
const FAILED = "Сервис недоступен. Попробуйте ещё раз.";

/** Выпустить ключ. Значение уходит только в ответ action и показывается один раз — в URL и cookie его нет. */
export async function createKey(): Promise<KeyState> {
  try {
    const created = await apiFetchAuthed<ApiKeyCreated>("/v1/api-keys", { method: "POST" });
    revalidatePath("/app/settings");
    return { key: created.key, prefix: created.prefix };
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && (e.status === 403 || e.status === 422)) return { error: e.detail };
    console.error("ключ не выпущен", e);
    return { error: `Не удалось выпустить ключ. ${FAILED}` };
  }
}

export async function revokeKey(id: string): Promise<void> {
  try {
    await apiFetchAuthed<void>(`/v1/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
  } catch (e) {
    unstable_rethrow(e);
    if (!(e instanceof ApiError && (e.status === 404 || e.status === 403))) console.error("ключ не отозван", e);
  }
  revalidatePath("/app/settings");
}

export async function createWebhook(_prev: WebhookState, form: FormData): Promise<WebhookState> {
  const url = String(form.get("url") ?? "").trim();
  const events = form.getAll("events").map(String).filter((e): e is WebhookEvent => EVENTS.includes(e as WebhookEvent));
  if (!url) return { error: "Введите адрес", url };
  if (events.length === 0) return { error: "Выберите хотя бы одно событие", url };
  try {
    const created = await apiFetchAuthed<WebhookCreated>("/v1/webhooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, events }),
    });
    revalidatePath("/app/settings");
    return { secret: created.secret };
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && e.status === 422) return { error: e.detail, url };
    console.error("вебхук не добавлен", e);
    return { error: `Не удалось добавить вебхук. ${FAILED}`, url };
  }
}

export async function deleteWebhook(id: string): Promise<void> {
  try {
    await apiFetchAuthed<void>(`/v1/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" });
  } catch (e) {
    unstable_rethrow(e);
    if (!(e instanceof ApiError && e.status === 404)) console.error("вебхук не удалён", e);
  }
  revalidatePath("/app/settings");
}
