"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { apiFetchAuthed } from "@/lib/session";
import type { RoutingRule, RoutingRules } from "@/lib/types";

export type RulesState = { error?: string; saved?: boolean };

const OPS = new Set(["eq", "ne", "gt", "ge", "lt", "le", "contains", "in_dictionary", "empty", "not_empty"]);

/** Заменяет весь список правил. Тело проверяет API; здесь только форма данных. */
export async function saveRules(rules: RoutingRule[]): Promise<RulesState> {
  if (!Array.isArray(rules)) return { error: "Не удалось прочитать правила." };
  const clean: RoutingRule[] = [];
  for (const r of rules) {
    clean.push({
      name: String(r.name ?? "").trim(),
      schema: r.schema ? String(r.schema) : null,
      conditions: (r.conditions ?? [])
        .filter((c) => OPS.has(c.op))
        .map((c) => ({
          field: String(c.field ?? "").trim(),
          op: c.op,
          ...(c.op === "empty" || c.op === "not_empty" ? {} : { value: String(c.value ?? "").trim() }),
        })),
      export_to: (r.export_to ?? []).map(String),
      require_approval: Boolean(r.require_approval),
      notify: (r.notify ?? []).map(String),
    });
  }
  const nameless = clean.findIndex((r) => !r.name);
  if (nameless >= 0) return { error: `Правило ${nameless + 1}: введите название.` };
  const noField = clean.findIndex((r) => r.conditions.some((c) => !c.field));
  if (noField >= 0) return { error: `Правило «${clean[noField].name}»: выберите поле в условии.` };
  try {
    await apiFetchAuthed<RoutingRules>("/v1/routing-rules", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules: clean }),
    });
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof ApiError && e.status === 403) return { error: "Правила меняет владелец аккаунта." };
    if (e instanceof ApiError && e.status === 422) return { error: e.detail };
    console.error("правила не сохранены", e);
    return { error: "Не удалось сохранить. Сервис недоступен, попробуйте ещё раз." };
  }
  revalidatePath("/app/rules");
  return { saved: true };
}
