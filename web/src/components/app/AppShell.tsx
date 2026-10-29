"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Logo } from "@/components/ui";

const NAV = [
  { href: "/app", label: "Очередь" },
  { href: "/app/upload", label: "Загрузить" },
  { href: "/app/dictionaries", label: "Справочники" },
  { href: "/app/integrations", label: "Подключения" },
  { href: "/app/rules", label: "Правила" },
  { href: "/app/settings", label: "API и вебхуки" },
];

function isCurrent(pathname: string, href: string): boolean {
  return href === "/app" ? pathname === "/app" : pathname === href || pathname.startsWith(`${href}/`);
}

/** Оболочка кабинета: верхняя полоса (логотип, клиент, режим модели, выход) и навигация. */
export function AppShell({
  tenantName,
  extractor,
  children,
}: {
  tenantName: string;
  extractor: string;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const mode = extractor === "ollama" ? "Локально" : "Облако";

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:border-2 focus:border-ink focus:bg-card focus:px-4 focus:py-3"
      >
        К содержанию
      </a>
      <header className="border-b-2 border-ink bg-paper">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 sm:px-8">
          <Logo />
          <p className="min-w-0 flex-1 truncate font-medium" title={tenantName}>
            {tenantName}
          </p>
          <p className="border-2 border-ink bg-card px-2 py-0.5 font-mono text-sm">
            <span className="sr-only">Режим модели: </span>
            {mode}
          </p>
          <form method="post" action="/app/logout">
            <button
              type="submit"
              className="inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text"
            >
              Выйти
            </button>
          </form>
        </div>
        <nav aria-label="Кабинет" className="border-t-2 border-ink bg-card">
          <ul className="mx-auto flex max-w-6xl flex-wrap gap-x-2 px-2 sm:px-6">
            {NAV.map((n) => {
              const current = isCurrent(pathname, n.href);
              return (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    aria-current={current ? "page" : undefined}
                    className={`inline-flex min-h-11 items-center border-b-4 px-3 text-base font-medium hover:text-accent-text ${
                      current ? "border-accent" : "border-transparent"
                    }`}
                  >
                    {n.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 py-8 sm:px-8">
        {children}
      </main>
    </>
  );
}
