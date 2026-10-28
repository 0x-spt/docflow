"use server";

import { revalidatePath } from "next/cache";
import { redirect, unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { isUuid } from "@/lib/ids";
import { apiFetchAuthed } from "@/lib/session";
import type { ConnectorInfo, Destination, TestResult } from "@/lib/types";

export type SaveState = {
  error?: string;
  /** Ошибки по полям настроек: имя поля -> текст */
  fieldErrors?: Record<string, string>;
  saved?: boolean;
  /** Несекретные значения для повторного показа после ошибки. Секреты сюда не попадают никогда. */
  name?: string;
  config?: Record<string, string>;
};

const FAILED = "Сервис недоступен. Попробуйте ещё раз.";
const FORBIDDEN = "Подключениями управляет владелец аккаунта.";
const LIST = "/app/integrations";
const NUMBER_RE = /^-?\d+([.,]\d+)?$/;

/** Коды ошибок для ?err=… — страница показывает по ним фиксированный текст. */
export type IntegrationErr = "forbidden" | "failed";

/** keepEmpty: у Google Таблиц пустое название колонки значит «поле включено, название по умолчанию». */
function parseMapping(raw: string, keepEmpty: boolean): Record<string, string> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw || "{}");
  } catch {
    return {};
  }
  const out: Record<string, string> = {};
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === "string" && (keepEmpty || v.trim())) out[k] = v.trim();
    }
  }
  return out;
}

/** Создать (id пуст) или изменить подключение. Поля берутся из каталога: чужих имён в запрос не попадёт. */
export async function saveDestination(_prev: SaveState, form: FormData): Promise<SaveState> {
  const kind = String(form.get("kind") ?? "");
  const id = String(form.get("id") ?? "");
  const name = String(form.get("name") ?? "").trim();
  if (id && !isUuid(id)) return { error: "Подключение не найдено." };

  let connector: ConnectorInfo | undefined;
  try {
    const catalog = await apiFetchAuthed<ConnectorInfo[]>("/v1/connectors");
    connector = catalog.find((c) => c.kind === kind);
  } catch (e) {
    unstable_rethrow(e);
    console.error("каталог подключений недоступен", e);
    return { error: FAILED, name };
  }
  if (!connector) return { error: "Неизвестный тип подключения.", name };

  const rawConfig: Record<string, string> = {};
  const config: Record<string, string | number> = {};
  const fieldErrors: Record<string, string> = {};
  for (const f of connector.config_fields) {
    const v = String(form.get(`c_${f.name}`) ?? "").trim();
    rawConfig[f.name] = v;
    if (v === "") continue;
    if (f.type === "number") {
      if (!NUMBER_RE.test(v)) fieldErrors[f.name] = "Введите число, например 25 или 0,5.";
      else config[f.name] = Number(v.replace(",", "."));
    } else config[f.name] = v;
  }
  if (Object.keys(fieldErrors).length > 0) {
    return { error: "Исправьте отмеченные поля.", fieldErrors, name, config: rawConfig };
  }
  const secrets: Record<string, string> = {};
  for (const f of connector.secret_fields) {
    const v = String(form.get(`s_${f.name}`) ?? "");
    if (v.trim() !== "") secrets[f.name] = v;
  }
  if (!name) return { error: "Введите название подключения.", name, config: rawConfig };
  const mapping = parseMapping(String(form.get("mapping") ?? ""), kind === "google_sheets");

  let newId: string | null = null;
  try {
    if (id) {
      // без секретов — API оставляет сохранённые
      await apiFetchAuthed<Destination>(`/v1/destinations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, config, mapping, ...(Object.keys(secrets).length ? { secrets } : {}) }),
      });
    } else {
      const created = await apiFetchAuthed<Destination>("/v1/destinations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, name, config, secrets, mapping }),
      });
      newId = created.id;
    }
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && e.status === 403) return { error: FORBIDDEN, name, config: rawConfig };
    if (e instanceof ApiError && e.status === 422) return { error: e.detail, name, config: rawConfig };
    if (e instanceof ApiError && e.status === 404) return { error: "Подключение не найдено.", name, config: rawConfig };
    console.error("подключение не сохранено", e);
    return { error: `Не удалось сохранить. ${FAILED}`, name, config: rawConfig };
  }
  revalidatePath(LIST);
  if (newId) redirect(`${LIST}/${newId}?created=1`);
  return { saved: true, name, config: rawConfig };
}

/** Проверка подключения: ответ API {ok, message} показывается как есть (сообщения без секретов). */
export async function testDestination(id: string): Promise<TestResult> {
  if (!isUuid(id)) return { ok: false, message: "Подключение не найдено." };
  try {
    return await apiFetchAuthed<TestResult>(`/v1/destinations/${id}/test`, { method: "POST" });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && (e.status === 403 || e.status === 422)) return { ok: false, message: e.detail };
    if (e instanceof ApiError && e.status === 404) return { ok: false, message: "Подключение не найдено." };
    console.error("проверка подключения не выполнена", e);
    return { ok: false, message: `Не удалось выполнить проверку. ${FAILED}` };
  }
}

async function finish(back: string, err: IntegrationErr | null): Promise<never> {
  revalidatePath(LIST);
  redirect(err ? `${back}${back.includes("?") ? "&" : "?"}err=${err}` : back);
}

export async function setDestinationActive(id: string, active: boolean, from: "list" | "item"): Promise<void> {
  const target = from === "item" && isUuid(id) ? `${LIST}/${id}` : LIST;
  let err: IntegrationErr | null = null;
  if (!isUuid(id)) err = "failed";
  else {
    try {
      await apiFetchAuthed<Destination>(`/v1/destinations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active }),
      });
    } catch (e) {
      unstable_rethrow(e);
      err = e instanceof ApiError && e.status === 403 ? "forbidden" : "failed";
      if (!(e instanceof ApiError)) console.error("статус подключения не изменён", e);
    }
  }
  await finish(target, err);
}

export async function deleteDestination(id: string): Promise<void> {
  let err: IntegrationErr | null = null;
  if (!isUuid(id)) err = "failed";
  else {
    try {
      await apiFetchAuthed<void>(`/v1/destinations/${id}`, { method: "DELETE" });
    } catch (e) {
      unstable_rethrow(e);
      if (e instanceof ApiError && e.status === 403) err = "forbidden";
      else if (!(e instanceof ApiError && e.status === 404)) {
        err = "failed";
        if (!(e instanceof ApiError)) console.error("подключение не удалено", e);
      }
    }
  }
  await finish(LIST, err);
}
