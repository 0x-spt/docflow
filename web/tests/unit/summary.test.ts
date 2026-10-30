import assert from "node:assert/strict";
import { test } from "node:test";

import { changeSummary, plural } from "../../src/lib/field-builder/summary.ts";
import type { DocSchema, SchemaField } from "../../src/lib/types.ts";

const f = (name: string, type: SchemaField["type"] = "string", extra: Partial<SchemaField> = {}): SchemaField => ({
  name,
  type,
  ...extra,
});
const schema = (fields: SchemaField[], rules: DocSchema["rules"] = []): DocSchema => ({
  slug: "doc_x",
  version: 1,
  title: "Док",
  description: "",
  fields,
  rules,
});

test("changeSummary: no changes", () => {
  const s = schema([f("a"), f("b")]);
  assert.equal(changeSummary(s, structuredClone(s)), "изменений нет");
});

test("changeSummary: поля", () => {
  const before = schema([f("a"), f("b"), f("c")]);
  const after = schema([f("a", "integer"), f("c"), f("d"), f("e")]);
  assert.equal(changeSummary(before, after), "добавлено 2 поля, удалено 1, тип изменён у 1");
  assert.equal(changeSummary(before, schema([f("a"), f("b")])), "удалено 1 поле");
});

test("changeSummary: правила", () => {
  const r1 = { kind: "date_order", params: { earlier: "a", later: "b" } };
  const r2 = { kind: "date_order", params: { earlier: "b", later: "c" } };
  assert.equal(changeSummary(schema([f("a")], [r1]), schema([f("a")], [r2])), "добавлено правил: 1, удалено правил: 1");
});

test("changeSummary: названия, новый тип", () => {
  const before = schema([f("a")]);
  assert.equal(changeSummary(before, schema([f("a", "string", { title: "А", description: "?" })])), "изменены названия и подсказки");
  assert.equal(changeSummary(null, schema([f("a"), f("b"), f("c")])), "новый тип документа: 3 поля");
});

test("plural", () => {
  const forms: [string, string, string] = ["поле", "поля", "полей"];
  assert.equal(plural(1, forms), "поле");
  assert.equal(plural(2, forms), "поля");
  assert.equal(plural(5, forms), "полей");
  assert.equal(plural(21, forms), "поле");
  assert.equal(plural(12, forms), "полей");
});
