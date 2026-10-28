/** Крупная моноширинная цифра с подписью. `tone="alert"` — красная цифра (на проверке, ошибки). */
export function Stat({
  value,
  label,
  tone = "default",
}: {
  value: string | number;
  label: string;
  tone?: "default" | "alert";
}) {
  return (
    <div className="border-2 border-ink bg-card p-4">
      <div
        className={`font-mono text-5xl font-medium leading-none tabular-nums ${tone === "alert" ? "text-accent-text" : ""}`}
      >
        {value}
      </div>
      <div className="mt-2 text-sm text-muted">{label}</div>
    </div>
  );
}
