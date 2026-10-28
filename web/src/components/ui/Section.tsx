import type { ReactNode } from "react";

/** Раздел страницы: номер `[NN]`, заголовок, мета-метка справа. `size="display"` — крупный вариант для лендинга. */
export function Section({
  num,
  title,
  meta,
  children,
  id,
  size = "default",
}: {
  num: string;
  title: string;
  meta?: string;
  children?: ReactNode;
  id?: string;
  size?: "default" | "display";
}) {
  const headingId = id ? `${id}-title` : undefined;
  const display = size === "display";
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      className={`border-t-2 border-ink ${display ? "scroll-mt-20 py-16 sm:py-24" : "py-10"}`}
    >
      <div
        className={`flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 ${display ? "mb-10 sm:mb-14" : "mb-6"}`}
      >
        <div className={display ? "flex flex-col gap-4" : "flex items-baseline gap-3"}>
          <span className="font-mono text-base font-medium text-accent-text">[{num}]</span>
          <h2
            id={headingId}
            className={
              display
                ? "max-w-4xl text-4xl font-semibold leading-[1.02] tracking-[-0.035em] text-balance sm:text-6xl"
                : "text-3xl font-semibold tracking-tight"
            }
          >
            {title}
          </h2>
        </div>
        {meta ? <span className="font-mono text-sm uppercase text-muted">{meta}</span> : null}
      </div>
      {children}
    </section>
  );
}
