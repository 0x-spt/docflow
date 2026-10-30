// Сводка правок схемы для подтверждения сохранения.
import type { DocSchema, RuleSpec, SchemaColumn, SchemaField } from "../types";

/** Русское склонение: plural(5, ["поле", "поля", "полей"]) -> "полей". */
export function plural(n: number, forms: [string, string, string]): string {
  const m100 = Math.abs(n) % 100;
  const m10 = m100 % 10;
  if (m100 >= 11 && m100 <= 14) return forms[2];
  if (m10 === 1) return forms[0];
  if (m10 >= 2 && m10 <= 4) return forms[1];
  return forms[2];
}

const FIELD_FORMS: [string, string, string] = ["поле", "поля", "полей"];

const ruleKey = (r: RuleSpec, owner: string): string => `${owner}|${r.kind}|${JSON.stringify(r.params)}`;

/** Правила документа и правила полей из `common`: у добавленных и удалённых полей правила уходят вместе с полем. */
function ruleKeys(s: DocSchema, common: ReadonlySet<string>): string[] {
  const keys = (s.rules ?? []).map((r) => ruleKey(r, ""));
  for (const f of s.fields) {
    if (common.has(f.name)) for (const r of f.rules ?? []) keys.push(ruleKey(r, f.name));
  }
  return keys;
}

/** Порядок общих имён в a и b различается. */
function reordered(a: readonly string[], b: readonly string[]): boolean {
  const inB = new Set(b);
  const inA = new Set(a);
  const x = a.filter((n) => inB.has(n));
  const y = b.filter((n) => inA.has(n));
  return x.some((n, i) => n !== y[i]);
}

/** JSON с отсортированными ключами; null и отсутствие ключа равны. */
function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v && typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>)
      .filter(([, x]) => x !== null && x !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, x]) => `${JSON.stringify(k)}:${canonical(x)}`).join(",")}}`;
  }
  return JSON.stringify(v ?? null);
}

/** Мультимножественная разница: сколько ключей из a нет в b. */
function missing(a: string[], b: string[]): number {
  const left = new Map<string, number>();
  for (const k of b) left.set(k, (left.get(k) ?? 0) + 1);
  let n = 0;
  for (const k of a) {
    const c = left.get(k) ?? 0;
    if (c > 0) left.set(k, c - 1);
    else n++;
  }
  return n;
}

function labelsDiffer(a: SchemaColumn | SchemaField, b: SchemaColumn | SchemaField): boolean {
  return (a.title ?? "") !== (b.title ?? "") || (a.description ?? "") !== (b.description ?? "");
}

/** Прочие настройки поля, кроме названий и правил: обязательность, варианты, колонки (порядок колонок — отдельно). */
function settings(f: SchemaField): string {
  return JSON.stringify({
    required: !!f.required,
    values: f.values ?? null,
    columns: (f.columns ?? [])
      .map((c) => [c.name, c.type, c.values ?? null] as const)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  });
}

export function changeSummary(before: DocSchema | null, after: DocSchema): string {
  if (!before) {
    const n = after.fields.length;
    return `новый тип документа: ${n} ${plural(n, FIELD_FORMS)}`;
  }
  const old = new Map(before.fields.map((f) => [f.name, f]));
  const now = new Set(after.fields.map((f) => f.name));
  let added = 0;
  let removed = 0;
  let retyped = 0;
  let labels = before.title !== after.title || before.description !== after.description;
  let other = false;
  let columnsMoved = false;
  for (const f of after.fields) {
    const o = old.get(f.name);
    if (!o) {
      added++;
      continue;
    }
    if (o.type !== f.type) retyped++;
    else if (settings(o) !== settings(f)) other = true;
    if (labelsDiffer(o, f)) labels = true;
    if (reordered((o.columns ?? []).map((c) => c.name), (f.columns ?? []).map((c) => c.name))) columnsMoved = true;
    const prevCols = new Map((o.columns ?? []).map((c) => [c.name, c]));
    for (const c of f.columns ?? []) {
      const prev = prevCols.get(c.name);
      if (prev && labelsDiffer(prev, c)) labels = true;
    }
  }
  for (const f of before.fields) if (!now.has(f.name)) removed++;

  const common = new Set(after.fields.filter((f) => old.has(f.name)).map((f) => f.name));
  const rulesAdded = missing(ruleKeys(after, common), ruleKeys(before, common));
  const rulesRemoved = missing(ruleKeys(before, common), ruleKeys(after, common));

  const parts: string[] = [];
  let nounUsed = false;
  const count = (verb: string, n: number): void => {
    if (!n) return;
    parts.push(nounUsed ? `${verb} ${n}` : `${verb} ${n} ${plural(n, FIELD_FORMS)}`);
    nounUsed = true;
  };
  count("добавлено", added);
  count("удалено", removed);
  if (retyped) parts.push(`тип изменён у ${retyped}`);
  if (rulesAdded) parts.push(`добавлено правил: ${rulesAdded}`);
  if (rulesRemoved) parts.push(`удалено правил: ${rulesRemoved}`);
  if (labels) parts.push("изменены названия и подсказки");
  if (other) parts.push("изменены настройки полей");
  if (reordered(before.fields.map((f) => f.name), after.fields.map((f) => f.name))) parts.push("изменён порядок полей");
  if (columnsMoved) parts.push("изменён порядок колонок");
  // всё, что сводка не называет поимённо, — одной строкой: иначе «Сохранить» недоступна при настоящей правке
  if (!parts.length && canonical(before) !== canonical(after)) parts.push("изменены другие настройки");
  return parts.length ? parts.join(", ") : "изменений нет";
}
