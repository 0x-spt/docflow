"use server";

import { revalidatePath } from "next/cache";
import { redirect, unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { isDocId } from "@/lib/ids";
import { buildData } from "@/lib/review-form";
import { apiFetchAuthed } from "@/lib/session";
import type { DocSchema, DocumentList, DocumentPayload, ReviewRequest } from "@/lib/types";

export type ReviewState = { error?: string; conflict?: boolean };

const NOTE_MAX = 1000;
const FAILED = "Не удалось сохранить проверку: сервис недоступен. Попробуйте ещё раз.";

/** Подтвердить документ с данными из формы (полный объект по схеме). */
export async function approve(id: string, _prev: ReviewState, form: FormData): Promise<ReviewState> {
  return review(id, "approve", form);
}

/** Отклонить документ: нужна причина, она уходит в вебхук и видна в кабинете. */
export async function reject(id: string, _prev: ReviewState, form: FormData): Promise<ReviewState> {
  return review(id, "reject", form);
}

async function review(id: string, decision: ReviewRequest["decision"], form: FormData): Promise<ReviewState> {
  if (!isDocId(id)) return { error: "Документ не найден" };
  const note = String(form.get("note") ?? "").trim();
  if (note.length > NOTE_MAX) return { error: `Комментарий длиннее ${NOTE_MAX} символов` };
  if (decision === "reject" && !note) return { error: "Укажите причину отклонения" };

  const path = `/v1/documents/${encodeURIComponent(id)}`;
  try {
    const body: ReviewRequest = { decision };
    if (note) body.note = note;
    if (decision === "approve") {
      // схему берём по документу на сервере: форма присылает только значения
      const doc = await apiFetchAuthed<DocumentPayload>(path);
      if (doc.status !== "needs_review") return { conflict: true };
      const schema = await apiFetchAuthed<DocSchema>(
        `/v1/schemas/${encodeURIComponent(doc.schema.slug)}?version=${doc.schema.version}`,
      );
      body.data = buildData(schema, form);
    }
    await apiFetchAuthed<DocumentPayload>(`${path}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    unstable_rethrow(e); // редирект на /auth/expired при 401
    if (e instanceof ApiError && e.status === 409) return { conflict: true };
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) return { error: e.detail };
    console.error("проверка документа не сохранена", id, e);
    return { error: FAILED };
  }

  let next = "/app";
  try {
    const list = await apiFetchAuthed<DocumentList>("/v1/documents?status=needs_review&limit=1");
    if (list.items[0]) next = `/app/documents/${list.items[0].id}`;
  } catch (e) {
    unstable_rethrow(e);
    console.error("не удалось найти следующий документ на проверку", e); // проверка уже сохранена — в очередь
  }
  redirect(next);
}

export type RetryState = { error?: string; ok?: boolean };

/** «Повторить» неудавшуюся выгрузку: 409 — она уже ушла или ещё в работе. */
export async function retryExport(docId: string, exportId: string): Promise<RetryState> {
  if (!isDocId(docId) || !isDocId(exportId)) return { error: "Выгрузка не найдена." };
  try {
    await apiFetchAuthed(`/v1/exports/${encodeURIComponent(exportId)}/retry`, { method: "POST" });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && e.status === 409) {
      revalidatePath(`/app/documents/${docId}`);
      return { error: "Эту выгрузку уже нельзя повторить: она выполнена или ещё в работе." };
    }
    if (e instanceof ApiError && e.status === 403) return { error: "Повторять выгрузки может владелец аккаунта." };
    if (e instanceof ApiError && e.status === 404) return { error: "Выгрузка не найдена." };
    console.error("повтор выгрузки не поставлен", e);
    return { error: "Не удалось повторить: сервис недоступен. Попробуйте ещё раз." };
  }
  revalidatePath(`/app/documents/${docId}`);
  return { ok: true };
}
