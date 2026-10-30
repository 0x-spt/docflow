import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { errorTargets } from "../../src/lib/field-builder/errors.ts";
import type { DocSchema } from "../../src/lib/types.ts";

const invoice: DocSchema = JSON.parse(readFileSync(new URL("./fixtures/builtin.json", import.meta.url), "utf8")).invoice_ru;

test("errorTargets: путь до колонки, правила поля, правила документа", () => {
  assert.deepEqual(errorTargets([{ loc: ["body", "fields", 2, "columns", 1], msg: "dup" }], invoice), {
    "fields.2.columns.1": "dup",
  });
  assert.deepEqual(errorTargets([{ loc: ["body", "fields", 1, "rules", 0], msg: "bad" }], invoice), { "fields.1": "bad" });
  assert.deepEqual(errorTargets([{ loc: ["body", "rules", 0], msg: "r" }], invoice), { "rules.0": "r" });
});

test("errorTargets: несколько ошибок на ключ и префикс", () => {
  const t = errorTargets(
    [
      { loc: ["body", "fields", 1], msg: "Value error, a" },
      { loc: ["body", "fields", 1, "rules", 2], msg: "b" },
    ],
    invoice,
  );
  assert.deepEqual(t, { "fields.1": "a; b" });
});

test("errorTargets: без пути", () => {
  const i = invoice.fields.findIndex((x) => x.name === "supplier");
  assert.deepEqual(errorTargets([{ loc: ["body"], msg: "Value error, «supplier»: pattern не компилируется" }], invoice), {
    [`fields.${i}`]: "«supplier»: pattern не компилируется",
  });
  assert.deepEqual(
    errorTargets([{ loc: ["body"], msg: "Value error, account_matches_bik: bik=«x» — нет такого поля" }], invoice),
    { "rules.2": "account_matches_bik: bik=«x» — нет такого поля" },
  );
  assert.deepEqual(errorTargets([{ loc: [], msg: "что-то" }], invoice), { general: "что-то" });
});
