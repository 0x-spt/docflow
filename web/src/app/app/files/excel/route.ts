import { proxyFile } from "@/lib/proxy";

const SLUG_RE = /^[A-Za-z0-9_-]{1,100}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const STATUSES = new Set(["ok", "needs_review", "all"]);

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
  if (!SLUG_RE.test(schema)) return bad("Выберите тип документа.");
  if (!DATE_RE.test(from) || Number.isNaN(Date.parse(from))) return bad("Укажите начало периода.");
  if (!DATE_RE.test(to) || Number.isNaN(Date.parse(to))) return bad("Укажите конец периода.");
  if (!STATUSES.has(status)) return bad("Неизвестный статус документов.");
  const q = new URLSearchParams({ schema, from, to, status });
  return proxyFile(`/v1/exports/excel?${q}`);
}
