"use server";

import { unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { apiFetchAuthed } from "@/lib/session";
import type { FieldLabels } from "@/lib/types";

export type LabelsState = { saved?: boolean; error?: string; fieldErrors?: Record<string, string> };

const FAILED = "Не удалось сохранить названия: сервис недоступен. Попробуйте ещё раз.";
const SLUG_RE = /^[a-z0-9_-]{1,80}$/;

/** Сохранить названия и поля очереди целиком: `label:<ключ>` — название, `queue` — отмеченные поля. */
export async function saveLabels(slug: string, _prev: LabelsState, form: FormData): Promise<LabelsState> {
  if (!SLUG_RE.test(slug)) return { error: "Некорректный тип документа" };
  const labels: Record<string, string> = {};
  for (const [name, value] of form.entries()) {
    if (!name.startsWith("label:")) continue;
    const text = String(value).trim();
    if (text) labels[name.slice("label:".length)] = text; // пустое — название по умолчанию
  }
  const queue_fields = form.getAll("queue").map(String);
  try {
    await apiFetchAuthed<FieldLabels>(`/v1/schemas/${encodeURIComponent(slug)}/labels`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ labels, queue_fields }),
    });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && (e.status === 422 || e.status === 404)) {
      return { error: e.detail, fieldErrors: e.fieldErrors };
    }
    if (e instanceof ApiError && e.status === 403) return { error: "Названия меняет владелец аккаунта." };
    console.error("названия полей не сохранены", slug, e);
    return { error: FAILED };
  }
  // без revalidatePath: форма уже показывает сохранённое, перемонтирование стёрло бы сообщение
  return { saved: true };
}
