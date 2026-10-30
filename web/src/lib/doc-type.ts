// Подпись типа документа: до автоопределения у документа служебный тип `auto`.
export const AUTO_SLUG = "auto";
/** Ответ модели «не знаю» при автоопределении: такой slug у типа зарезервирован, как и `auto`. */
export const UNKNOWN_SLUG = "unknown";

export function docTypeLabel(slug: string, title: string | undefined, status: string): string {
  if (slug !== AUTO_SLUG) return title ?? slug;
  return status === "queued" || status === "processing" ? "Определяется…" : "Тип не определён";
}

/**
 * Сколько превью показывать. `pages` считается при приёме, а файлы страниц появляются только при подготовке к модели:
 * у failed, не дошедшего до извлечения (`model` пуст), их может не быть, и пробуем одну, чтобы не выводить N плашек.
 */
export function previewCount(status: string, model: string | null, pages: number | null): number {
  if (status === "failed" && !model) return 1;
  return Math.max(pages ?? 0, 1);
}
