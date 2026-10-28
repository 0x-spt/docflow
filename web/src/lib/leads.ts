// Общее для формы заявки (клиент) и server action: варианты списков и состояние формы.
import type { LeadTarget, LeadVolume } from "./types";

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

/** «Куда выгружать данные»: коды как в API (POST /v1/leads, поле targets). */
export const TARGETS: { value: LeadTarget; label: string }[] = [
  { value: "1c", label: "1С" },
  { value: "bitrix24", label: "Битрикс24" },
  { value: "amocrm", label: "amoCRM" },
  { value: "google_sheets", label: "Google Таблицы" },
  { value: "excel", label: "Excel" },
  { value: "own_system", label: "Своя система" },
  { value: "unknown", label: "Пока не знаю" },
];

/** Состояние формы для `useActionState`; `values` — введённое, чтобы не терять его при ошибке (targets — коды через запятую). */
export interface LeadState {
  ok: boolean;
  errors?: Record<string, string>;
  message?: string;
  values?: Record<string, string>;
}

export const INITIAL_LEAD_STATE: LeadState = { ok: false };
