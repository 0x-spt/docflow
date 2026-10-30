// Расход страниц по тарифу: текст, полоска и предупреждение для кабинета.
import type { Usage } from "./types";

export interface UsageView {
  text: string;
  /** Доля лимита 0–100; null — тариф без лимита */
  percent: number | null;
  tone: "ok" | "warn" | "over";
  note: string | null;
}

const WARN_AT = 80;
const OVER_AT = 100;

export function usageView(u: Usage): UsageView {
  const head = `Тариф «${u.plan_title}»: использовано ${u.pages_used}`;
  if (u.pages_limit === null || u.pages_limit <= 0) {
    return { text: `${head} страниц в этом месяце`, percent: null, tone: "ok", note: null };
  }
  const raw = (u.pages_used * 100) / u.pages_limit;
  const text = `${head} из ${u.pages_limit} страниц в этом месяце`;
  const percent = Math.min(100, raw);
  if (raw >= OVER_AT) {
    return { text, percent, tone: "over", note: "Лимит страниц на месяц исчерпан: напишите нам, чтобы сменить тариф" };
  }
  if (raw >= WARN_AT) return { text, percent, tone: "warn", note: "Использовано 80% лимита или больше" };
  return { text, percent, tone: "ok", note: null };
}
