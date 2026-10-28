// Имена полей формы проверки и сборка `data` из неё. Общие для формы (клиент) и server action.

import type { DocSchema, Issue, SchemaColumn } from "./types";

/** Коды замечаний, которые только сообщают (справочник поправил написание), а не требуют внимания. */
export const INFO_CODES = new Set(["corrected"]);

/** Замечания по пути поля («total», «works[0].price»): проблемы — красным, `corrected` — нейтрально. */
export type FieldNotes = Record<string, { problems: string[]; info: string[] }>;

export function notesByField(issues: Issue[]): FieldNotes {
  const notes: FieldNotes = {};
  for (const issue of issues) {
    if (!issue.field || !issue.message) continue;
    const entry = (notes[issue.field] ??= { problems: [], info: [] });
    (INFO_CODES.has(issue.code ?? "") ? entry.info : entry.problems).push(issue.message);
  }
  return notes;
}

/** Скалярное поле верхнего уровня. */
export const scalarName = (field: string) => `f:${field}`;
/** Ключи строк таблицы по порядку (по одному скрытому input на строку). */
export const rowsName = (table: string) => `r:${table}`;
/** Ячейка таблицы. */
export const cellName = (table: string, row: string, column: string) => `c:${table}:${row}:${column}`;

function scalarValue(spec: SchemaColumn, form: FormData, name: string): string | boolean | null {
  if (spec.type === "boolean") return form.get(name) === "true";
  const raw = form.get(name);
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  // числа и даты уходят текстом как введены: бэкенд приводит их так же, как ответ модели
  return value === "" ? null : value;
}

/** ПОЛНЫЙ объект данных по схеме: пустое → null, таблица — массив строк со всеми колонками. */
export function buildData(schema: DocSchema, form: FormData): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  for (const field of schema.fields) {
    if (field.type !== "table") {
      data[field.name] = scalarValue(field as SchemaColumn, form, scalarName(field.name));
      continue;
    }
    const rows = form.getAll(rowsName(field.name)).filter((r): r is string => typeof r === "string");
    data[field.name] = rows.map((row) =>
      Object.fromEntries(
        (field.columns ?? []).map((c) => [c.name, scalarValue(c, form, cellName(field.name, row, c.name))]),
      ),
    );
  }
  return data;
}
