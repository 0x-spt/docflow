import { proxyFile } from "@/lib/proxy";

const SLUG_RE = /^[A-Za-z0-9_-]{1,100}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const STATUSES = new Set(["ok", "needs_review", "all"]);
// IANA-имя пояса («Asia/Novosibirsk», «UTC», «Etc/GMT+3»); существует ли он, проверит API
const TZ_RE = /^[A-Za-z][A-Za-z0-9_+\-]*(?:\/[A-Za-z0-9_+\-]+){0,2}$/;

/** Настоящая ли дата: «2026-02-31» разбирается Date, но сдвигается на март. */
function realDate(v: string): boolean {
  const t = Date.parse(v);
  return DATE_RE.test(v) && !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === v;
}

function bad(text: string): Response {
  return new Response(text, {
    status: 400,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "private, no-store" },
  });
}

/** Реестр документов в Excel через сайт: сессию подставляет сайт, параметры проверяются до запроса к API. */
export async function GET(request: Request) {
  const sp = new URL(request.url).searchParams;
  const schema = sp.get("schema") ?? "";
  const from = sp.get("from") ?? "";
  const to = sp.get("to") ?? "";
  const status = sp.get("status") || "ok";
  const tz = sp.get("tz") ?? "";
  if (!SLUG_RE.test(schema)) return bad("Выберите тип документа.");
  if (!realDate(from)) return bad("Укажите начало периода.");
  if (!realDate(to)) return bad("Укажите конец периода.");
  if (to < from) return bad("Конец периода раньше начала.");
  if (!STATUSES.has(status)) return bad("Неизвестный статус документов.");
  if (tz && (tz.length > 64 || !TZ_RE.test(tz))) return bad("Неизвестный часовой пояс.");
  const q = new URLSearchParams({ schema, from, to, status });
  if (tz) q.set("tz", tz);
  return proxyFile(`/v1/exports/excel?${q}`);
}
