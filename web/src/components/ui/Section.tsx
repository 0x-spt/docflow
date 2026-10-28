import type { ReactNode } from "react";

/** Раздел страницы: номер `[NN]`, заголовок, мета-метка справа. */
export function Section({
  num,
  title,
  meta,
  children,
  id,
}: {
  num: string;
  title: string;
  meta?: string;
  children?: ReactNode;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section id={id} aria-labelledby={headingId} className="border-t-2 border-ink py-10">
      <div className="mb-6 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-base font-medium text-accent-text">[{num}]</span>
          <h2 id={headingId} className="text-3xl font-semibold tracking-tight">
            {title}
          </h2>
        </div>
        {meta ? <span className="font-mono text-sm uppercase text-muted">{meta}</span> : null}
      </div>
      {children}
    </section>
  );
}
