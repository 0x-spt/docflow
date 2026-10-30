import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  deleteBlockers,
  fromSchema,
  move,
  newColumn,
  newDoc,
  newField,
  toSchema,
  validateDoc,
  withNames,
  type EditorDoc,
  type EditorField,
} from "../../src/lib/field-builder/model.ts";
import { sumRule } from "../../src/lib/field-builder/rules.ts";
import type { DocSchema } from "../../src/lib/types.ts";

const builtin: Record<string, DocSchema> = JSON.parse(
  readFileSync(new URL("./fixtures/builtin.json", import.meta.url), "utf8"),
);

const invoice = (): EditorDoc => fromSchema(builtin.invoice_ru);

function addField(doc: EditorDoc, patch: Partial<EditorField>): EditorDoc {
  const [next, f] = newField(doc);
  return { ...next, fields: [...next.fields, { ...f, ...patch }] };
}

function patchField(doc: EditorDoc, name: string, patch: Partial<EditorField>): EditorDoc {
  return { ...doc, fields: doc.fields.map((f) => (f.name === name ? { ...f, ...patch } : f)) };
}

const fieldOf = (s: DocSchema, name: string) => s.fields.find((f) => f.name === name)!;

test("round-trip keeps builtin schemas intact", () => {
  for (const s of Object.values(builtin)) assert.deepStrictEqual(toSchema(fromSchema(s)), s);
});

test("newDoc has number and issued", () => {
  const s = toSchema(newDoc());
  assert.deepEqual(
    s.fields.map((f) => f.name),
    ["number", "issued"],
  );
  assert.deepEqual(fieldOf(s, "issued").rules, [{ kind: "date_not_future", params: {} }]);
});

test("new field gets translit name and title as description", () => {
  let doc = addField(newDoc(), { title: "Номер договора" });
  let s = toSchema(withNames(doc));
  const f = s.fields[2];
  assert.equal(f.name, "nomer_dogovora");
  assert.equal(f.description, "Номер договора");
  doc = addField(withNames(doc), { title: "Номер договора" });
  s = toSchema(withNames(doc));
  assert.equal(s.fields[3].name, "nomer_dogovora_2");
});

test("new rows get deterministic uids", () => {
  const [d1, f1] = newField(newDoc());
  const [, f2] = newField(d1);
  assert.equal(f1.uid, "n0");
  assert.equal(f2.uid, "n1");
  const doc = invoice();
  assert.equal(doc.fields[0].uid, "f0");
  assert.equal(doc.fields[10].columns[2].uid, "f10c2");
});

test("existing names never change", () => {
  const doc = withNames(patchField(invoice(), "supplier", { title: "Продавец" }));
  const s = toSchema(doc);
  assert.equal(s.fields[2].name, "supplier");
  assert.equal(s.fields[2].title, "Продавец");
});

test("withNames rewrites doc rule refs of renamed new fields", () => {
  let doc = withNames(addField(invoice(), { title: "Итого" }));
  const i = doc.fields.length - 1;
  assert.equal(doc.fields[i].name, "itogo");
  doc = { ...doc, rules: [...doc.rules, sumRule("items", "amount", "itogo")] };
  doc = withNames({ ...doc, fields: doc.fields.map((f, k) => (k === i ? { ...f, title: "Итого к оплате" } : f)) });
  assert.equal(doc.fields[i].name, "itogo_k_oplate");
  assert.equal(doc.rules[doc.rules.length - 1].params.total, "itogo_k_oplate");
  // чужие правила не тронуты
  assert.deepEqual(doc.rules.slice(0, -1), builtin.invoice_ru.rules);
});

test("withNames rewrites column refs of renamed new columns", () => {
  let doc = invoice();
  const t = doc.fields.findIndex((f) => f.name === "items");
  const [next, c] = newColumn(doc);
  const addCol = (d: EditorDoc, title: string): EditorDoc => ({
    ...d,
    fields: d.fields.map((f, k) =>
      k === t ? { ...f, columns: [...f.columns.filter((x) => x.uid !== c.uid), { ...(f.columns.find((x) => x.uid === c.uid) ?? c), title }] } : f,
    ),
  });
  doc = withNames(addCol(next, "НДС"));
  assert.equal(doc.fields[t].columns.at(-1)!.name, "nds");
  doc = { ...doc, rules: [sumRule("items", "nds", "vat")] };
  doc = withNames(addCol(doc, "Сумма НДС"));
  assert.deepEqual(doc.rules[0].params, { table: "items", column: "summa_nds", total: "vat", tolerance: 0.01 });
});

test("check replaces in place and keeps params", () => {
  let s = toSchema(patchField(invoice(), "supplier_inn", { check: "regex", checkParam: "\\d{10}" }));
  assert.deepEqual(fieldOf(s, "supplier_inn").rules, [{ kind: "regex", params: { pattern: "\\d{10}" } }]);
  s = toSchema(patchField(invoice(), "supplier_inn", { check: "none" }));
  assert.deepEqual(fieldOf(s, "supplier_inn").rules, []);
  s = toSchema(patchField(invoice(), "issued", { type: "string" }));
  assert.deepEqual(fieldOf(s, "issued").rules, []);
});

test("check keeps fuzzy of same-kind dictionary and other rules in place", () => {
  const base: DocSchema = {
    ...builtin.passport_ru,
    fields: [
      {
        name: "city",
        type: "string",
        title: "Город",
        description: "",
        values: null,
        required: false,
        columns: null,
        rules: [
          { kind: "date_not_older", params: { days: 3 } },
          { kind: "dictionary", params: { name: "cities", fuzzy: true } },
          { kind: "inn", params: {} },
        ],
      },
    ],
    rules: [],
  };
  const doc = fromSchema(base);
  assert.equal(doc.fields[0].check, "dictionary");
  assert.equal(doc.fields[0].checkParam, "cities");
  const s = toSchema({ ...doc, fields: [{ ...doc.fields[0], checkParam: "towns" }] });
  assert.deepEqual(s.fields[0].rules, [
    { kind: "date_not_older", params: { days: 3 } },
    { kind: "dictionary", params: { name: "towns", fuzzy: true } },
    { kind: "inn", params: {} },
  ]);
});

test("validateDoc flags table type change referenced by rule", () => {
  const errs = validateDoc(patchField(invoice(), "items", { type: "string" }), []);
  assert.ok("rules.0" in errs);
  assert.ok("rules.1" in errs);
  assert.match(errs["rules.0"], /^Правило ссылается на то, чего уже нет: «/);
  assert.deepEqual(validateDoc(invoice(), []), {});
});

test("validateDoc messages", () => {
  const empty: EditorDoc = { ...newDoc(), fields: [] };
  let errs = validateDoc(empty, []);
  assert.equal(errs.title, "Введите название типа");
  assert.equal(errs.slug, "Служебное имя: 2–40 символов, латинские строчные буквы, цифры и «_»");
  assert.equal(errs.fields, "Добавьте хотя бы одно поле");

  errs = validateDoc({ ...newDoc(), title: "Счёт", slug: "invoice_ru" }, ["invoice_ru"]);
  assert.equal(errs.slug, "Тип с таким служебным именем уже есть");

  // у существующего типа slug не проверяется
  assert.equal(validateDoc(invoice(), ["invoice_ru"]).slug, undefined);

  let doc = addField(invoice(), { title: "Ещё таблица", type: "table" });
  const i = doc.fields.length - 1;
  errs = validateDoc(doc, []);
  assert.equal(errs[`fields.${i}.type`], "Таблица в типе может быть только одна");
  assert.equal(errs[`fields.${i}.columns`], "Добавьте хотя бы одну колонку");

  doc = addField(newDoc(), {});
  errs = validateDoc({ ...doc, title: "Т", slug: "tt" }, []);
  assert.equal(errs["fields.2.title"], "Введите название поля");

  doc = addField(newDoc(), { title: "Статус", type: "enum", values: " \n  \n" });
  doc = addField(doc, { title: "Город", check: "dictionary" });
  doc = addField(doc, { title: "Код", check: "regex", checkParam: "  " });
  errs = validateDoc({ ...doc, title: "Т", slug: "tt" }, []);
  assert.equal(errs["fields.2.values"], "Добавьте хотя бы один вариант");
  assert.equal(errs["fields.3.check"], "Выберите справочник");
  assert.equal(errs["fields.4.check"], "Введите шаблон");
});

test("enum values are trimmed, deduplicated, only for enum", () => {
  let doc = addField(newDoc(), { title: "Статус", type: "enum", values: " a \n\nb\na\n" });
  let s = toSchema(doc);
  assert.deepEqual(s.fields[2].values, ["a", "b"]);
  assert.equal(s.fields[2].columns, null);
  doc = { ...doc, fields: doc.fields.map((f, k) => (k === 2 ? { ...f, type: "string" } : f)) };
  s = toSchema(doc);
  assert.equal(s.fields[2].values, null);
});

test("deleteBlockers", () => {
  const doc = invoice();
  const vat = doc.fields.findIndex((f) => f.name === "vat");
  assert.deepEqual(deleteBlockers(doc, vat), ["«Всего к оплате» = «Итого без НДС» + «НДС»"]);
  const items = doc.fields.findIndex((f) => f.name === "items");
  const unit = doc.fields[items].columns.findIndex((c) => c.name === "unit");
  assert.deepEqual(deleteBlockers(doc, items, unit), []);
});

test("move at list edges leaves list unchanged", () => {
  assert.deepEqual(move([1, 2, 3], 0, -1), [1, 2, 3]);
  assert.deepEqual(move([1, 2, 3], 2, 1), [1, 2, 3]);
  assert.deepEqual(move([1, 2, 3], 0, 1), [2, 1, 3]);
  assert.deepEqual(move([1, 2, 3], 2, -1), [1, 3, 2]);
});
