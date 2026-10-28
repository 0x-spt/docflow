import Link from "next/link";

import { Logo } from "@/components/ui";

const LINK = "inline-flex min-h-11 items-center underline-offset-4 hover:underline decoration-2";

/** Подвал: реквизиты и контакты — плейсхолдеры до заполнения оператором. */
export function SiteFooter() {
  return (
    <footer className="border-t-2 border-ink bg-ink text-paper">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-8 md:grid-cols-12">
        <div className="md:col-span-5">
          <Logo className="[&_sup]:text-paper" />
          <p className="mt-3 max-w-sm text-paper/80">
            Фото и сканы документов — в чистый PDF и проверенные данные в вашей системе.
          </p>
        </div>
        <div className="md:col-span-4">
          <h2 className="font-mono text-sm uppercase text-paper/70">Контакты</h2>
          <ul className="mt-3 space-y-1 font-mono">
            <li>[EMAIL]</li>
            <li>[ТЕЛЕФОН]</li>
            <li className="pt-3 font-sans text-paper/80">[ОПЕРАТОР ПД], ИНН [ИНН]</li>
          </ul>
        </div>
        <nav aria-label="Ссылки в подвале" className="md:col-span-3">
          <ul className="flex flex-col">
            <li>
              <Link href="/audit" className={LINK}>
                Заявка на аудит
              </Link>
            </li>
            <li>
              <Link href="/privacy" className={LINK}>
                Политика обработки ПД
              </Link>
            </li>
            <li>
              <Link href="/login" className={LINK}>
                Войти в кабинет
              </Link>
            </li>
          </ul>
        </nav>
      </div>
    </footer>
  );
}
