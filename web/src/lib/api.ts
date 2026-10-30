import "server-only";

import { headers } from "next/headers";

import { getSessionToken } from "./session";
import type { ValidationItem } from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    /** Ошибки по полям формы: ключ поля («vat», «items.qty») или «queue_fields» -> текст. */
    public readonly fieldErrors: Record<string, string> = {},
    /** Ошибки валидации FastAPI как есть (`loc` и `msg`); строковый `detail` — пусто. */
    public readonly errors: ValidationItem[] = [],
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

type FetchInit = RequestInit & { auth?: boolean };

function apiBase(): string {
  return (process.env.DOCFLOW_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

/**
 * IP клиента для лимитов API; вне контекста запроса (сборка) — null.
 * Первому адресу X-Forwarded-For верить нельзя — его пишет сам клиент. Берём X-Real-IP, который ставит наш
 * прокси (`proxy_set_header X-Real-IP $remote_addr`), иначе последний адрес X-Forwarded-For — его дописал
 * ближайший к сайту узел. Ничего нет — X-Client-IP не отправляем.
 */
async function clientIp(): Promise<string | null> {
  try {
    const h = await headers();
    const real = h.get("x-real-ip")?.trim();
    if (real) return real;
    const last = h.get("x-forwarded-for")?.split(",").pop()?.trim();
    return last || null;
  } catch {
    return null;
  }
}

/** Запрос к API от имени сайта. Cookie браузера в API не уходят: токен сессии — только в Authorization. */
export async function apiFetchRaw(path: string, init: FetchInit = {}): Promise<Response> {
  const { auth = true, headers: extra, ...rest } = init;
  const h = new Headers(extra);
  if (auth) {
    const token = await getSessionToken();
    if (token) h.set("Authorization", `Bearer ${token}`);
  }
  const internal = process.env.DOCFLOW_INTERNAL_TOKEN;
  if (internal) h.set("X-Internal-Token", internal);
  const ip = await clientIp();
  if (ip) h.set("X-Client-IP", ip);
  return fetch(`${apiBase()}${path}`, { ...rest, headers: h, cache: "no-store" });
}

/** `detail` бывает строкой (наши ошибки) или списком ошибок валидации FastAPI. */
function detailOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : null))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  return fallback;
}

/** Ошибки валидации по полям: `loc` = [body, labels, <ключ>] или [body, queue_fields, …]. Строковый `detail` — пусто. */
function fieldErrorsOf(body: unknown): Record<string, string> {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const out: Record<string, string> = {};
  if (!Array.isArray(detail)) return out;
  for (const d of detail) {
    if (!d || typeof d !== "object") continue;
    const { loc, msg } = d as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const key = loc.includes("labels") ? String(loc[loc.length - 1]) : loc.includes("queue_fields") ? "queue_fields" : null;
    if (key && !(key in out)) out[key] = msg;
  }
  return out;
}

/** Элементы `detail[]` с путём `loc` и текстом `msg`; строковый `detail` — пусто. */
function validationItemsOf(body: unknown): ValidationItem[] {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return [];
  const out: ValidationItem[] = [];
  for (const d of detail) {
    if (!d || typeof d !== "object") continue;
    const { loc, msg } = d as { loc?: unknown; msg?: unknown };
    if (typeof msg !== "string") continue;
    const path = Array.isArray(loc)
      ? loc.filter((x): x is string | number => typeof x === "string" || typeof x === "number")
      : [];
    out.push({ loc: path, msg });
  }
  return out;
}

/** JSON-запрос: 204 → undefined, не-2xx → ApiError с `detail` из тела. */
export async function apiFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  const res = await apiFetchRaw(path, init);
  if (res.status === 204) return undefined as T;
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // тело не JSON — остаётся статусный текст
    }
    throw new ApiError(
      res.status,
      detailOf(body, res.statusText || `ошибка ${res.status}`),
      fieldErrorsOf(body),
      validationItemsOf(body),
    );
  }
  return (await res.json()) as T;
}
