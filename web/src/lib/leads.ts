// Общее для формы заявки (клиент) и server action: варианты списков и состояние формы.
import type { LeadVolume } from "./types";

export const NICHES = [
  "Зуботехническая лаборатория",
  "Бухгалтерия и аутсорс",
  "Логистика и склад",
  "Автосервис",
  "Медицина",
  "Кадры",
  "Другое",
] as const;

export const VOLUMES: { value: LeadVolume; label: string }[] = [
  { value: "lt100", label: "до 100" },
  { value: "100-1000", label: "100–1 000" },
  { value: "1000-10000", label: "1 000–10 000" },
  { value: "gt10000", label: "больше 10 000" },
  { value: "unknown", label: "пока не знаю" },
];

/** Состояние формы для `useActionState`; `values` — введённое, чтобы не терять его при ошибке. */
export interface LeadState {
  ok: boolean;
  errors?: Record<string, string>;
  message?: string;
  values?: Record<string, string>;
}

export const INITIAL_LEAD_STATE: LeadState = { ok: false };
