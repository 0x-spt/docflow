import type { Metadata } from "next";

import { PublicPage } from "@/components/landing/PublicPage";
import { Button } from "@/components/ui";

export const metadata: Metadata = { title: "Страница не найдена — DocFlow" };

export default function NotFound() {
  return (
    <PublicPage>
      <div className="py-16 sm:py-28">
        <p className="font-mono text-[6rem] font-medium leading-none tracking-[-0.06em] text-accent-text sm:text-[11rem]">
          404
        </p>
        <h1 className="mt-6 text-4xl font-semibold tracking-tight sm:text-5xl">Такой страницы нет</h1>
        <p className="mt-4 max-w-xl text-lg text-muted">
          Ссылка устарела или в адресе опечатка. Начните с главной или оставьте заявку на аудит.
        </p>
        <div className="mt-10 flex flex-col gap-4 sm:flex-row">
          <Button href="/" variant="primary">
            На главную
          </Button>
          <Button href="/audit" variant="ghost">
            Заявка на аудит
          </Button>
        </div>
      </div>
    </PublicPage>
  );
}
