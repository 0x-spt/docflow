import "server-only";

import { unstable_rethrow } from "next/navigation";

import { apiFetchAuthed } from "@/lib/session";
import type { DocSchema, MappingSchema, SchemaListItem } from "@/lib/types";

const MAX_SCHEMAS = 30;

/** Схемы тенанта в виде строк маппинга. Колонки таблицы — «таблица.колонка» (так их ждёт 1С). Сбой одной схемы её пропускает. */
export async function loadMappingSchemas(): Promise<MappingSchema[]> {
  let list: SchemaListItem[];
  try {
    list = await apiFetchAuthed<SchemaListItem[]>("/v1/schemas");
  } catch (e) {
    unstable_rethrow(e);
    return [];
  }
  const details = await Promise.all(
    list.slice(0, MAX_SCHEMAS).map(async (s) => {
      try {
        return await apiFetchAuthed<DocSchema>(
          `/v1/schemas/${encodeURIComponent(s.slug)}?version=${s.version}`,
        );
      } catch (e) {
        unstable_rethrow(e);
        return null;
      }
    }),
  );
  return details
    .filter((d): d is DocSchema => d !== null)
    .map((d) => ({
      slug: d.slug,
      title: d.title,
      rows: d.fields.flatMap((f) =>
        f.type === "table"
          ? (f.columns ?? []).map((c) => ({
              key: `${f.name}.${c.name}`,
              label: `${f.title || f.name}: ${c.title || c.name}`,
              group: "Таблица",
            }))
          : [{ key: f.name, label: f.title || f.name }],
      ),
    }));
}
