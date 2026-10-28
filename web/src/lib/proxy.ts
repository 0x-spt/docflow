import "server-only";

import { apiFetchRaw } from "./api";

// content-length не передаём: fetch мог распаковать сжатое тело, длина бы не совпала
const PASS_HEADERS = ["content-type", "content-disposition"];

/**
 * Файл из API (PDF, превью страницы) для браузера: тело потоком, без буферизации.
 * 401 → 303 на /auth/expired (Location относительный, см. /auth/expired), 404 → 404,
 * другие ошибки — тем же статусом с текстом из API.
 */
export async function proxyFile(path: string): Promise<Response> {
  const upstream = await apiFetchRaw(path);
  if (upstream.status === 401) {
    // тело ошибки маленькое — дочитываем (cancel() на ответе undici в standalone зависал)
    await upstream.text().catch(() => "");
    return new Response(null, { status: 303, headers: { Location: "/auth/expired" } });
  }
  if (!upstream.ok) {
    let detail = upstream.status === 404 ? "Файл не найден" : `Ошибка ${upstream.status}`;
    try {
      const body = (await upstream.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // тело не JSON — остаётся общий текст
    }
    return new Response(detail, {
      status: upstream.status,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "private, no-store" },
    });
  }
  const headers = new Headers({ "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" });
  for (const name of PASS_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, { status: 200, headers });
}
