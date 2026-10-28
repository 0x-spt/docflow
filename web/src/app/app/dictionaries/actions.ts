"use server";

import { redirect, unstable_rethrow } from "next/navigation";
import { revalidatePath } from "next/cache";

import { ApiError } from "@/lib/api";
import { apiFetchAuthed } from "@/lib/session";
import type { Dictionary, DictionaryEntry } from "@/lib/types";

export type DictState = { error?: string; saved?: boolean; name?: string; title?: string };

const NAME_RE = /^[a-z0-9_]{2,40}$/;
const FAILED = "Не удалось сохранить справочник: сервис недоступен. Попробуйте ещё раз.";

const path = (name: string) => `/v1/dictionaries/${encodeURIComponent(name)}`;

function put(name: string, title: string, entries: DictionaryEntry[]) {
  return apiFetchAuthed<Dictionary>(path(name), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, entries }),
  });
}

/** Создать пустой справочник и открыть редактор. Существующее имя не перезаписываем. */
export async function createDictionary(_prev: DictState, form: FormData): Promise<DictState> {
  const name = String(form.get("name") ?? "").trim();
  const title = String(form.get("title") ?? "").trim();
  const keep = { name, title };
  if (!NAME_RE.test(name)) {
    return { ...keep, error: "Имя: 2–40 символов, только латинские строчные буквы, цифры и «_»" };
  }
  if (!title) return { ...keep, error: "Введите название" };
  try {
    const existing = await apiFetchAuthed<Dictionary[]>("/v1/dictionaries");
    if (existing.some((d) => d.name === name)) return { ...keep, error: `Справочник «${name}» уже есть` };
    await put(name, title, []);
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && (e.status === 422 || e.status === 409)) return { ...keep, error: e.detail };
    console.error("справочник не создан", e);
    return { ...keep, error: FAILED };
  }
  redirect(`/app/dictionaries/${name}`);
}

/** Сохранить справочник целиком: поля `value` и `aliases` идут парами в порядке строк. */
export async function saveDictionary(name: string, _prev: DictState, form: FormData): Promise<DictState> {
  const title = String(form.get("title") ?? "").trim();
  const values = form.getAll("value").map(String);
  const aliases = form.getAll("aliases").map(String);
  const entries: DictionaryEntry[] = [];
  values.forEach((v, i) => {
    const value = v.trim();
    const list = (aliases[i] ?? "")
      .split(",")
      .map((a) => a.trim())
      .filter(Boolean);
    if (value || list.length) entries.push({ value, aliases: list }); // полностью пустые строки пропускаем
  });
  try {
    await put(name, title, entries);
  } catch (e) {
    unstable_rethrow(e);
    // pydantic добавляет английский префикс к русскому тексту ошибки
    if (e instanceof ApiError && (e.status === 422 || e.status === 404)) return { error: e.detail.replace(/^Value error, /, "") };
    console.error("справочник не сохранён", name, e);
    return { error: FAILED };
  }
  // без revalidatePath: редактор уже показывает сохранённое, а его перемонтирование стёрло бы сообщение
  return { saved: true };
}

/** Подсказка → в справочник. Справочник перечитываем: правки в другой вкладке не должны теряться. */
async function applySuggestion(name: string, value: string, nearest: string | null) {
  let error: string | null = null;
  try {
    const dict = await apiFetchAuthed<Dictionary>(path(name));
    const entries = (dict.entries ?? []).map((e) => ({ value: e.value, aliases: [...e.aliases] }));
    const target = nearest ? entries.find((e) => e.value === nearest) : undefined;
    if (nearest && target) {
      if (!target.aliases.includes(value)) target.aliases.push(value);
    } else {
      entries.push({ value, aliases: [] });
    }
    await put(name, dict.title, entries);
  } catch (e) {
    unstable_rethrow(e);
    error = e instanceof ApiError ? e.detail : FAILED;
    if (!(e instanceof ApiError)) console.error("подсказка не применена", name, e);
  }
  revalidatePath(`/app/dictionaries/${name}`);
  redirect(`/app/dictionaries/${name}${error ? `?error=${encodeURIComponent(error)}` : ""}`);
}

export async function addAlias(name: string, value: string, nearest: string): Promise<void> {
  await applySuggestion(name, value, nearest);
}

export async function addEntry(name: string, value: string): Promise<void> {
  await applySuggestion(name, value, null);
}

export async function deleteDictionary(name: string): Promise<void> {
  try {
    await apiFetchAuthed<void>(path(name), { method: "DELETE" });
  } catch (e) {
    unstable_rethrow(e);
    if (!(e instanceof ApiError && e.status === 404)) {
      console.error("справочник не удалён", name, e);
      redirect(`/app/dictionaries/${name}?error=${encodeURIComponent("Не удалось удалить справочник")}`);
    }
  }
  redirect("/app/dictionaries");
}
