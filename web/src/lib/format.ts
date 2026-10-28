// Форматирование значений для кабинета. Без server-only: нужно и форме проверки (клиентский компонент).

import type { SchemaColumn } from "./types";

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Часовой пояс отметок времени в кабинете (сервер в контейнере живёт в UTC). */
const TIME_ZONE = process.env.DOCFLOW_TIMEZONE || "Europe/Moscow";

/** «2026-10-24» → «24.10.2026»; не ISO-дата — как есть. */
export function isoToRu(value: string): string {
  const m = ISO_DATE.exec(value);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : value;
}

/** Время события: «28.10.2026, 08:18». */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    timeZone: TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Число для поля ввода: десятичная запятая, без разделителей тысяч (бэкенд разберёт и «12 500,50»). */
export function numberToInput(value: number): string {
  return String(value).replace(".", ",");
}

/** Значение поля как текст для ввода: дата → ДД.ММ.ГГГГ, число → с запятой, null → пусто. */
export function valueToInput(spec: { type: string }, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return numberToInput(value);
  if (typeof value === "string") return spec.type === "date" ? isoToRu(value) : value;
  if (typeof value === "boolean") return value ? "да" : "нет";
  return JSON.stringify(value);
}

/** Значение для просмотра: как для ввода, но число — с разделителями тысяч, пустое — «—». */
export function valueToDisplay(spec: { type: string }, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString("ru-RU", { maximumFractionDigits: 6 });
  return valueToInput(spec, value);
}

/** Значение из списка документов (тип неизвестен): ISO-дата → ДД.ММ.ГГГГ, число — с разделителями. */
export function summaryValue(value: string | number): string {
  if (typeof value === "number") return value.toLocaleString("ru-RU", { maximumFractionDigits: 6 });
  return isoToRu(value);
}

const STOP_WORDS = [" как ", " после ", " по ", " в ", " через ", " — ", " – "];

/** Начало описания до первого разделителя вне «кавычек»: «Дата наряда (после «от»). …» → «Дата наряда». */
function descriptionHead(text: string): string {
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "«") depth++;
    else if (ch === "»") depth = Math.max(0, depth - 1);
    if (depth > 0) continue;
    if ("(:;,".includes(ch)) return text.slice(0, i);
    if (ch === "." && (i + 1 === text.length || text[i + 1] === " ")) return text.slice(0, i);
    if (ch === " " && STOP_WORDS.some((w) => text.startsWith(w, i))) return text.slice(0, i);
  }
  return text;
}

/**
 * Подпись поля. Заголовков в схеме нет, есть имя и описание для модели («Клиника как написана.»):
 * берём начало описания; нет описания или вышло слишком длинно — имя поля.
 */
export function fieldLabel(spec: Pick<SchemaColumn, "name" | "description">): string {
  const head = descriptionHead((spec.description ?? "").trim()).trim();
  const text = head && head.length <= 40 ? head : spec.name.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
