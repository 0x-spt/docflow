import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  accountBikRule,
  rulePhrase,
  ruleRefs,
  rowProductRule,
  rulesUsing,
  sumRule,
} from "../../src/lib/field-builder/rules.ts";
import type { DocSchema } from "../../src/lib/types.ts";

const builtin: Record<string, DocSchema> = JSON.parse(
  readFileSync(new URL("./fixtures/builtin.json", import.meta.url), "utf8"),
);
const invoice = builtin.invoice_ru;
const rules = invoice.rules ?? [];

function titleOf(key: string): string {
  const [name, col] = key.split(".");
  const f = invoice.fields.find((x) => x.name === name);
  if (!f) return key;
  if (col === undefined) return f.title ?? f.name;
  const c = f.columns?.find((x) => x.name === col);
  return c?.title ?? c?.name ?? key;
}

test("ruleRefs: таблица, колонка, итог", () => {
  assert.deepEqual(ruleRefs(rules[0]), ["items", "items.amount", "subtotal"]);
  assert.deepEqual(ruleRefs({ kind: "foo", params: { a: "b" } }), []);
  assert.deepEqual(ruleRefs({ kind: "total_consistent", params: { total: "t", vat: null } }), ["t"]);
});

test("rulesUsing", () => {
  assert.deepEqual(rulesUsing(rules, "vat"), [3]);
  assert.deepEqual(rulesUsing(rules, "items"), [0, 1, 3]);
  assert.deepEqual(rulesUsing(rules, "items.unit"), []);
});

test("rulePhrase", () => {
  assert.equal(rulePhrase(rules[0], titleOf), "Сумма колонки «Сумма» = «Итого без НДС»");
  assert.equal(
    rulePhrase({ kind: "total_consistent", params: { total: "total", table: "items", column: "amount" } }, titleOf),
    "«Всего к оплате» = сумма колонки «Сумма»",
  );
  assert.equal(rulePhrase({ kind: "foo", params: {} }, titleOf), "Правило foo");
  assert.equal(rulePhrase({ kind: "date_within", params: { earlier: "a", later: "b", max_days: 30 } }, (k) => k),
    "«b» не позже чем через 30 дн. после «a»");
});

test("конструкторы", () => {
  assert.deepEqual(sumRule("items", "amount", "total"), {
    kind: "table_sum_equals",
    params: { table: "items", column: "amount", total: "total", tolerance: 0.01 },
  });
  assert.deepEqual(rowProductRule("items", "qty", "price", "amount"), {
    kind: "table_row_product",
    params: { table: "items", qty: "qty", price: "price", amount: "amount" },
  });
  assert.deepEqual(accountBikRule("account", "bik"), {
    kind: "account_matches_bik",
    params: { account: "account", bik: "bik" },
  });
});
