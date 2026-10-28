import type { ReactNode } from "react";

import { SiteFooter } from "./SiteFooter";
import { SiteHeader } from "./SiteHeader";

/** Оболочка публичных страниц: ссылка «к содержанию», шапка, сетка с видимыми краями, подвал. */
export function PublicPage({ children }: { children: ReactNode }) {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:border-2 focus:border-ink focus:bg-card focus:px-4 focus:py-3"
      >
        К содержанию
      </a>
      <SiteHeader />
      <main id="main" className="mx-auto max-w-6xl px-4 sm:px-8 xl:border-x-2 xl:border-ink">
        {children}
      </main>
      <SiteFooter />
    </>
  );
}
