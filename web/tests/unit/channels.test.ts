import assert from "node:assert/strict";
import { test } from "node:test";

import { isOpenToAll, parseSenders } from "../../src/lib/channels.ts";

test("parseSenders: адреса и домены в нижнем регистре, разделители любые", () => {
  const r = parseSenders("A@Firma.ru, @firma.ru\nb@x.ru");
  assert.deepEqual(r.senders, ["a@firma.ru", "@firma.ru", "b@x.ru"]);
  assert.deepEqual(r.errors, []);
});

test("parseSenders: неверные записи попадают в errors", () => {
  const r = parseSenders("плохой, a@b");
  assert.deepEqual(r.errors, ["плохой", "a@b"]);
  assert.deepEqual(r.senders, []);
});

test("parseSenders: пусто и повторы", () => {
  assert.deepEqual(parseSenders(""), { senders: [], errors: [] });
  assert.deepEqual(parseSenders("a@x.ru A@X.ru").senders, ["a@x.ru"]);
  assert.equal(isOpenToAll([]), true);
  assert.equal(isOpenToAll(["a@x.ru"]), false);
});
