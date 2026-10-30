import assert from "node:assert/strict";
import { test } from "node:test";

import type { Usage } from "../../src/lib/types.ts";
import { usageView } from "../../src/lib/usage.ts";

const usage = (pages_used: number, pages_limit: number | null): Usage => ({
  plan: "start",
  plan_title: "Старт",
  pages_limit,
  pages_used,
  period_start: "2026-10-01T00:00:00+03:00",
  period_end: "2026-11-01T00:00:00+03:00",
});

test("79% — всё хорошо", () => {
  const v = usageView(usage(79, 100));
  assert.equal(v.tone, "ok");
  assert.equal(v.note, null);
  assert.equal(v.percent, 79);
  assert.equal(v.text, "Тариф «Старт»: использовано 79 из 100 страниц в этом месяце");
});

test("80% — предупреждение", () => {
  const v = usageView(usage(80, 100));
  assert.equal(v.tone, "warn");
  assert.equal(v.note, "Использовано больше 80% лимита");
});

test("100% и 120% — лимит исчерпан, процент не больше 100", () => {
  for (const used of [100, 120]) {
    const v = usageView(usage(used, 100));
    assert.equal(v.tone, "over");
    assert.equal(v.percent, 100);
    assert.ok(v.note?.includes("напишите нам, чтобы сменить тариф"));
  }
});

test("без лимита: процента нет, в тексте нет «из»", () => {
  const v = usageView(usage(1234, null));
  assert.equal(v.percent, null);
  assert.equal(v.tone, "ok");
  assert.equal(v.note, null);
  assert.equal(v.text, "Тариф «Старт»: использовано 1234 страниц в этом месяце");
  assert.ok(!v.text.includes(" из "));
});

test("ноль страниц", () => {
  const v = usageView(usage(0, 50));
  assert.equal(v.percent, 0);
  assert.equal(v.tone, "ok");
});
