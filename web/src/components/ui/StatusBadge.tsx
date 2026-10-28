export type DocStatus = "ok" | "needs_review" | "failed" | "queued" | "processing";

const LABELS: Record<DocStatus, string> = {
  ok: "Готово",
  needs_review: "Нужна проверка",
  failed: "Ошибка",
  queued: "В очереди",
  processing: "Обрабатывается",
};

const SQUARE: Record<DocStatus, string> = {
  ok: "bg-ink",
  needs_review: "bg-accent",
  failed: "bg-accent-text",
  queued: "bg-card",
  processing: "bg-muted",
};

/** Статус документа: квадратная метка и подпись по-русски (цвет не единственный носитель смысла). */
export function StatusBadge({ status }: { status: DocStatus }) {
  return (
    <span className="inline-flex items-center gap-2 font-mono text-sm font-medium">
      <span aria-hidden="true" className={`inline-block size-3 border-2 border-ink ${SQUARE[status]}`} />
      {LABELS[status]}
    </span>
  );
}
