/** Крупная моноширинная цифра с подписью. */
export function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="border-2 border-ink bg-card p-4">
      <div className="font-mono text-5xl font-medium leading-none tabular-nums">{value}</div>
      <div className="mt-2 text-sm text-muted">{label}</div>
    </div>
  );
}
