import assert from "node:assert/strict";
import { test } from "node:test";

import { AUTO_SLUG, docTypeLabel, previewCount } from "../../src/lib/doc-type.ts";

test("автоопределение в работе — «Определяется…»", () => {
  assert.equal(docTypeLabel(AUTO_SLUG, undefined, "queued"), "Определяется…");
  assert.equal(docTypeLabel(AUTO_SLUG, undefined, "processing"), "Определяется…");
});

test("автоопределение не сработало — «Тип не определён»", () => {
  assert.equal(docTypeLabel(AUTO_SLUG, undefined, "failed"), "Тип не определён");
});

test("превью: failed до извлечения — одна попытка, иначе все страницы", () => {
  assert.equal(previewCount("failed", null, 5), 1);
  assert.equal(previewCount("failed", "claude", 5), 5);
  assert.equal(previewCount("ok", "claude", 5), 5);
  assert.equal(previewCount("processing", null, 5), 5);
  assert.equal(previewCount("ok", "claude", null), 1);
  assert.equal(previewCount("failed", "claude", 0), 1);
});

test("обычный тип: название или slug", () => {
  assert.equal(docTypeLabel("invoice_ru", "Счёт", "ok"), "Счёт");
  assert.equal(docTypeLabel("invoice_ru", undefined, "ok"), "invoice_ru");
});
