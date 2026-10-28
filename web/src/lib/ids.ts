// id сущностей API (документы, ключи, вебхуки) — UUID; остальное в адресе отбрасываем до запроса к API
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Похоже ли значение на id сущности API (UUID). */
export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}

/** Похоже ли значение на id документа. */
export const isDocId = isUuid;
