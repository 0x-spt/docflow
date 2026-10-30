import { unstable_rethrow } from "next/navigation";

import { apiFetchAuthed } from "@/lib/session";
import type { Usage } from "@/lib/types";
import { usageView } from "@/lib/usage";

const BAR = { ok: "bg-ink", warn: "bg-accent", over: "bg-accent-text" };

/** Тариф и расход страниц за месяц. Сбой API не роняет страницу: блок просто не показывается. */
export async function UsageBlock() {
  let usage: Usage;
  try {
    usage = await apiFetchAuthed<Usage>("/v1/usage");
  } catch (e) {
    unstable_rethrow(e);
    console.error("не удалось получить тариф", e);
    return null;
  }
  const v = usageView(usage);
  return (
    <section aria-label="Тариф" className="flex flex-col gap-2 border-2 border-ink bg-card p-4">
      <p id="usage-text" className="font-medium">
        {v.text}
      </p>
      {v.percent !== null ? (
        <div
          role="progressbar"
          aria-labelledby="usage-text"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(v.percent)}
          className="h-3 w-full border-2 border-ink bg-paper"
        >
          <div className={`h-full ${BAR[v.tone]}`} style={{ width: `${v.percent}%` }} />
        </div>
      ) : null}
      {v.note ? (
        <p
          role={v.tone === "over" ? "alert" : "status"}
          className={`font-medium ${v.tone === "over" ? "text-accent-text" : ""}`}
        >
          {v.note}
        </p>
      ) : null}
    </section>
  );
}
