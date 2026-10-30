import assert from "node:assert/strict";
import { test } from "node:test";

import { slugFromTitle, toIdentifier, uniqueName } from "../../src/lib/field-builder/translit.ts";

test("toIdentifier: обычные названия", () => {
  assert.equal(toIdentifier("Номер договора"), "nomer_dogovora");
  assert.equal(toIdentifier("Пробег, км"), "probeg_km");
  assert.equal(toIdentifier("Объём щебня"), "obem_shchebnya");
  assert.equal(toIdentifier("ИНН"), "inn");
});

test("uniqueName / toIdentifier edge cases", () => {
  assert.equal(toIdentifier("№"), "field");
  assert.equal(toIdentifier("2-НДФЛ"), "2_ndfl");
  assert.equal(toIdentifier("а".repeat(80)).length, 40);
  assert.equal(uniqueName("summa", new Set(["summa"])), "summa_2");
  assert.equal(uniqueName("summa", new Set(["summa", "summa_2"])), "summa_3");
  assert.equal(uniqueName("summa", new Set()), "summa");
  const long = "x".repeat(40);
  const u = uniqueName(long, new Set([long]));
  assert.equal(u.length, 40);
  assert.ok(u.endsWith("_2"));
});

test("slugFromTitle", () => {
  assert.equal(slugFromTitle("Путевой лист"), "putevoy_list");
  assert.equal(slugFromTitle("Я"), "ya");
  assert.equal(slugFromTitle("!"), "doc");
  assert.equal(slugFromTitle("5"), "5_doc");
});
