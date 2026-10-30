import assert from "node:assert/strict";
import { test } from "node:test";

import { AUTO_SLUG, docTypeLabel } from "../../src/lib/doc-type.ts";

test("автоопределение в работе — «Определяется…»", () => {
  assert.equal(docTypeLabel(AUTO_SLUG, undefined, "queued"), "Определяется…");
  assert.equal(docTypeLabel(AUTO_SLUG, undefined, "processing"), "Определяется…");
});

test("автоопределение не сработало — «Тип не определён»", () => {
  assert.equal(docTypeLabel(AUTO_SLUG, undefined, "failed"), "Тип не определён");
});

test("обычный тип: название или slug", () => {
  assert.equal(docTypeLabel("invoice_ru", "Счёт", "ok"), "Счёт");
  assert.equal(docTypeLabel("invoice_ru", undefined, "ok"), "invoice_ru");
});
