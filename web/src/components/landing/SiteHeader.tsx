import Link from "next/link";

import { Button, Logo } from "@/components/ui";

const NAV = [
  { href: "/#systems", label: "Куда выгружаем" },
  { href: "/#how", label: "Как работает" },
  { href: "/#niches", label: "Ниши" },
  { href: "/#security", label: "Безопасность" },
  { href: "/#faq", label: "Вопросы" },
];

const LINK_LOOK = "min-h-11 items-center px-1 hover:text-accent-text hover:underline underline-offset-4";
const NAV_LINK = `inline-flex ${LINK_LOOK}`;

/**
 * Шапка публичных страниц. На узком экране якоря — в раскрывающемся меню на <details> (работает без JS);
 * ниже sm в меню уходит и «Войти», чтобы шапка помещалась в 320 px.
 */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-20 border-b-2 border-ink bg-paper">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-2 sm:gap-4 sm:px-8">
        <Logo />
        <nav aria-label="Разделы" className="hidden xl:block">
          <ul className="flex items-center gap-5 text-base">
            {NAV.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className={NAV_LINK}>
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="flex items-center gap-2 sm:gap-3">
          <Link href="/login" className={`${LINK_LOOK} hidden font-medium sm:inline-flex`}>
            Войти
          </Link>
          <Button href="/audit" variant="primary" className="px-3 sm:px-4">
            Аудит
          </Button>
          <details className="group relative xl:hidden">
            <summary className="flex min-h-11 min-w-11 cursor-pointer list-none items-center justify-center border-2 border-ink bg-card px-3 font-mono text-sm [&::-webkit-details-marker]:hidden">
              <span className="group-open:hidden">Меню</span>
              <span className="hidden group-open:inline">Закрыть</span>
            </summary>
            <nav
              aria-label="Разделы"
              className="absolute right-0 top-full mt-2 w-64 max-w-[calc(100vw-2rem)] border-2 border-ink bg-card p-2 shadow-hard"
            >
              <ul className="flex flex-col">
                {NAV.map((n) => (
                  <li key={n.href}>
                    <Link href={n.href} className={`${NAV_LINK} w-full px-3`}>
                      {n.label}
                    </Link>
                  </li>
                ))}
                <li className="mt-2 border-t-2 border-ink pt-2">
                  <Link href="/audit" className={`${NAV_LINK} w-full px-3 font-semibold`}>
                    Получить аудит
                  </Link>
                </li>
                <li className="sm:hidden">
                  <Link href="/login" className={`${NAV_LINK} w-full px-3`}>
                    Войти в кабинет
                  </Link>
                </li>
              </ul>
            </nav>
          </details>
        </div>
      </div>
    </header>
  );
}
