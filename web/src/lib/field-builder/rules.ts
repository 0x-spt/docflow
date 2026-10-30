// Правила документа: ссылки на поля, человеческие фразы, конструкторы.
import type { RuleSpec } from "../types";

// Держать в согласии с docflow/schemas/model.py (_DOC_RULE_PARAMS).
export const DOC_RULE_PARAMS: Record<string, Record<string, "field" | "table" | "column" | "int">> = {
  table_sum_equals: { table: "table", column: "column", total: "field" },
  table_row_product: { table: "table", qty: "column", price: "column", amount: "column" },
  table_product_sum: { table: "table", qty: "column", price: "column", total: "field" },
  date_order: { earlier: "field", later: "field" },
  date_within: { earlier: "field", later: "field", max_days: "int" },
  account_matches_bik: { account: "field", bik: "field" },
  total_consistent: { total: "field", subtotal: "field", vat: "field", table: "table", column: "column" },
};

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Ключи, на которые ссылается правило: поля и таблицы как есть, колонки как «таблица.колонка». */
export function ruleRefs(rule: RuleSpec): string[] {
  const spec = DOC_RULE_PARAMS[rule.kind];
  if (!spec) return [];
  const table = str(rule.params.table);
  const refs: string[] = [];
  for (const [param, kind] of Object.entries(spec)) {
    const v = str(rule.params[param]);
    if (!v || kind === "int") continue;
    if (kind === "column") {
      if (table) refs.push(`${table}.${v}`);
    } else {
      refs.push(v);
    }
  }
  return refs;
}

/** Индексы правил, ссылающихся на key; для таблицы — и на её колонки. */
export function rulesUsing(rules: RuleSpec[], key: string): number[] {
  const out: number[] = [];
  rules.forEach((r, i) => {
    if (ruleRefs(r).some((ref) => ref === key || ref.startsWith(`${key}.`))) out.push(i);
  });
  return out;
}

export function rulePhrase(rule: RuleSpec, titleOf: (key: string) => string): string {
  const p = rule.params;
  const T = (param: string): string => titleOf(str(p[param]));
  const col = (): string => titleOf(`${str(p.table)}.${str(p.column)}`);
  switch (rule.kind) {
    case "table_sum_equals":
      return `Сумма колонки «${col()}» = «${T("total")}»`;
    case "table_row_product":
      return `«${T("qty")}» × «${T("price")}» = «${T("amount")}» в каждой строке`;
    case "table_product_sum":
      return `Сумма «${T("qty")}» × «${T("price")}» по строкам = «${T("total")}»`;
    case "account_matches_bik":
      return `Счёт «${T("account")}» сходится с БИК «${T("bik")}»`;
    case "total_consistent": {
      let base = "";
      if (str(p.subtotal)) base = `«${T("subtotal")}»`;
      else if (str(p.table) && str(p.column)) base = `сумма колонки «${col()}»`;
      let s = `«${T("total")}»`;
      if (base) s += ` = ${base}`;
      if (str(p.vat)) s += ` + «${T("vat")}»`;
      return s;
    }
    case "date_order":
      return `«${T("earlier")}» не позже «${T("later")}»`;
    case "date_within":
      return `«${T("later")}» не позже чем через ${String(p.max_days)} дн. после «${T("earlier")}»`;
    default:
      return `Правило ${rule.kind}`;
  }
}

export function sumRule(table: string, column: string, total: string): RuleSpec {
  return { kind: "table_sum_equals", params: { table, column, total, tolerance: 0.01 } };
}

export function rowProductRule(table: string, qty: string, price: string, amount: string): RuleSpec {
  return { kind: "table_row_product", params: { table, qty, price, amount } };
}

export function accountBikRule(account: string, bik: string): RuleSpec {
  return { kind: "account_matches_bik", params: { account, bik } };
}
