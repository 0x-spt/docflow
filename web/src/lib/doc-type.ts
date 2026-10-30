// Подпись типа документа: до автоопределения у документа служебный тип `auto`.
export const AUTO_SLUG = "auto";

export function docTypeLabel(slug: string, title: string | undefined, status: string): string {
  if (slug !== AUTO_SLUG) return title ?? slug;
  return status === "queued" || status === "processing" ? "Определяется…" : "Тип не определён";
}
