import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { LoginForm } from "@/components/LoginForm";
import { PublicPage } from "@/components/landing/PublicPage";
import { ApiError, apiFetch } from "@/lib/api";
import { safeNext } from "@/lib/next-path";
import { getSessionToken } from "@/lib/session";
import type { Me } from "@/lib/types";

export const metadata: Metadata = { title: "Вход — DocFlow" };

/** Cookie есть — проверяем в API; в кабинет отправляем только при действительной сессии (иначе петля). */
async function hasValidSession(): Promise<boolean> {
  if (!(await getSessionToken())) return false;
  try {
    await apiFetch<Me>("/v1/me");
    return true;
  } catch (e) {
    if (!(e instanceof ApiError)) console.error("вход: проверка сессии не удалась", e);
    return false;
  }
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[]; expired?: string | string[] }>;
}) {
  const sp = await searchParams;
  const next = safeNext(Array.isArray(sp.next) ? sp.next[0] : sp.next);
  if (await hasValidSession()) redirect(next);
  const expired = sp.expired !== undefined;

  return (
    <PublicPage>
      <div className="mx-auto max-w-lg py-12 sm:py-20">
        <p className="font-mono text-base font-medium text-accent-text">[кабинет]</p>
        <h1 className="mt-4 text-5xl font-semibold leading-[0.95] tracking-[-0.04em]">Вход</h1>
        {expired ? (
          <p role="status" className="mt-6 border-2 border-ink bg-card p-4 font-medium">
            Сессия истекла, войдите снова
          </p>
        ) : null}
        <div className="mt-8">
          <LoginForm next={next} />
        </div>
        <p className="mt-6 text-base">
          Нет доступа?{" "}
          <Link href="/audit" className="underline decoration-2 underline-offset-4 hover:text-accent-text">
            Оставьте заявку на аудит
          </Link>
        </p>
      </div>
    </PublicPage>
  );
}
