"use server";

import { revalidatePath } from "next/cache";
import { redirect, unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { errorTargets } from "@/lib/field-builder/errors";
import { apiFetchAuthed } from "@/lib/session";
import type { DocSchema, FieldLabels } from "@/lib/types";

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
  // сводка «умолчание» шлёт форма: пустой список = очередь по умолчанию, следует за схемой
  const queue_fields = form.get("queue_default") ? [] : form.getAll("queue").map(String);
  try {
    await apiFetchAuthed<FieldLabels>(`/v1/schemas/${encodeURIComponent(slug)}/labels`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ labels, queue_fields }),
    });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && (e.status === 422 || e.status === 404 || e.status === 409)) {
      return { error: e.detail, fieldErrors: e.fieldErrors };
    }
    if (e instanceof ApiError && e.status === 403) return { error: "Названия меняет владелец аккаунта." };
    console.error("названия полей не сохранены", slug, e);
    return { error: FAILED };
  }
  // без revalidatePath: форма уже показывает сохранённое, перемонтирование стёрло бы сообщение
  return { saved: true };
}

export type SchemaSaveState = { error?: string; errors?: Record<string, string> };

const SCHEMA_SLUG_RE = /^[a-z0-9_]{2,40}$/;

/** Сохранить тип документа новой версией. `expectedVersion` — версия, с которой начал редактор (0 — новый тип). */
export async function saveSchema(schema: DocSchema, expectedVersion: number): Promise<SchemaSaveState> {
  if (!schema || typeof schema.slug !== "string" || !SCHEMA_SLUG_RE.test(schema.slug)) {
    return { error: "Некорректное служебное имя типа документа" };
  }
  if (!Array.isArray(schema.fields)) return { error: "Не удалось прочитать поля. Обновите страницу." };
  if (!Number.isInteger(expectedVersion) || expectedVersion < 0) {
    return { error: "Не удалось прочитать версию типа. Обновите страницу." };
  }
  let saved: DocSchema;
  try {
    saved = await apiFetchAuthed<DocSchema>(`/v1/schemas?expected_version=${expectedVersion}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(schema),
    });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && e.status === 422) {
      const errors = errorTargets(e.errors, schema);
      return { error: errors.general ?? "Сервер не принял схему: исправьте отмеченное.", errors };
    }
    if (e instanceof ApiError && (e.status === 409 || e.status === 404)) return { error: e.detail };
    if (e instanceof ApiError && e.status === 403) return { error: "Поля меняет владелец аккаунта." };
    console.error("схема не сохранена", schema.slug, e);
    return { error: "Не удалось сохранить: сервис недоступен. Попробуйте ещё раз." };
  }
  revalidatePath("/app/fields");
  redirect(`/app/fields?schema=${encodeURIComponent(saved.slug)}&saved=${saved.version}`);
}

export type ResetState = { error?: string };

/** Вернуть стандартные поля: тело встроенной схемы сохраняется новой версией поверх перекрытия компании. */
export async function resetSchema(slug: string, expectedVersion: number): Promise<ResetState> {
  if (typeof slug !== "string" || !SCHEMA_SLUG_RE.test(slug)) return { error: "Некорректное служебное имя типа документа" };
  if (!Number.isInteger(expectedVersion) || expectedVersion < 0) {
    return { error: "Не удалось прочитать версию типа. Обновите страницу." };
  }
  let saved: DocSchema;
  try {
    const builtin = await apiFetchAuthed<DocSchema>(`/v1/schemas/${encodeURIComponent(slug)}?raw=true&builtin=true`);
    saved = await apiFetchAuthed<DocSchema>(`/v1/schemas?expected_version=${expectedVersion}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(builtin),
    });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && (e.status === 409 || e.status === 404)) return { error: e.detail };
    if (e instanceof ApiError && e.status === 422) return { error: e.detail || "Сервер не принял стандартную схему." };
    if (e instanceof ApiError && e.status === 403) return { error: "Поля меняет владелец аккаунта." };
    console.error("стандартные поля не возвращены", slug, e);
    return { error: "Не удалось вернуть стандартные поля: сервис недоступен. Попробуйте ещё раз." };
  }
  revalidatePath("/app/fields");
  redirect(`/app/fields?schema=${encodeURIComponent(saved.slug)}&saved=${saved.version}`);
}
