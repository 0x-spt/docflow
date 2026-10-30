// Разбор 422 бэкенда: к какому месту редактора относится каждая ошибка.
import type { DocSchema, ValidationItem } from "../types";

const PREFIX = "Value error, ";

function isIndex(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

/** Ключ по пути loc (без ведущего "body") или null, если путь не указывает на поле/правило. */
function keyFromPath(path: (string | number)[]): string | null {
  if (path[0] === "fields" && isIndex(path[1])) {
    if (path[2] === "columns" && isIndex(path[3])) return `fields.${path[1]}.columns.${path[3]}`;
    return `fields.${path[1]}`;
  }
  if (path[0] === "rules" && isIndex(path[1])) return `rules.${path[1]}`;
  return null;
}

/** Без пути: по префиксу «<kind>:» -> правило документа, по первому «x» с именем поля -> поле, иначе общее. */
function keyFromText(text: string, sent: DocSchema): string {
  const m = /^([a-z0-9_]+):/.exec(text);
  if (m) {
    const j = (sent.rules ?? []).findIndex((r) => r.kind === m[1]);
    if (j >= 0) return `rules.${j}`;
  }
  for (const q of text.matchAll(/«([^»]*)»/g)) {
    const i = sent.fields.findIndex((f) => f.name === q[1]);
    if (i >= 0) return `fields.${i}`;
  }
  return "general";
}

export function errorTargets(items: ValidationItem[], sent: DocSchema): Record<string, string> {
  const out: Record<string, string> = {};
  for (const it of items) {
    const text = it.msg.startsWith(PREFIX) ? it.msg.slice(PREFIX.length) : it.msg;
    const path = it.loc[0] === "body" ? it.loc.slice(1) : it.loc;
    const key = keyFromPath(path) ?? (path.length === 0 ? keyFromText(text, sent) : "general");
    out[key] = key in out ? `${out[key]}; ${text}` : text;
  }
  return out;
}
