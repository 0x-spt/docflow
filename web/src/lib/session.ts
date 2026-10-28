import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ApiError, apiFetch } from "./api";
import type { Me } from "./types";

export const SESSION_COOKIE = "df_session";

/** Secure-cookie только в проде и если не отключено явно (COOKIE_SECURE=false — dev по http). */
function cookieSecure(): boolean {
  return process.env.NODE_ENV === "production" && process.env.COOKIE_SECURE !== "false";
}

export async function getSessionToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value ?? null;
}

/** Только из server action / route handler. Срок cookie = `expires_at` сессии. */
export async function setSession(token: string, expiresAt: string): Promise<void> {
  const jar = await cookies();
  jar.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: cookieSecure(),
    expires: new Date(expiresAt),
  });
}

/** Только из server action / route handler (в рендере страницы cookie менять нельзя). */
export async function clearSession(): Promise<void> {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
}

/**
 * Текущий пользователь или редирект на /login.
 * 401 (сессия истекла/отозвана) → /auth/expired. Другие ошибки API пробрасываются.
 */
export async function requireMe(): Promise<Me> {
  try {
    return await apiFetch<Me>("/v1/me");
  } catch (err) {
    // в рендере cookie не стереть: это делает route handler /auth/expired и ведёт на /login?expired=1
    if (err instanceof ApiError && err.status === 401) redirect("/auth/expired");
    throw err;
  }
}

/**
 * Запрос к API из страниц и server actions кабинета: 401 (сессия истекла/отозвана) → /auth/expired,
 * остальные ошибки пробрасываются как ApiError.
 */
export async function apiFetchAuthed<T>(path: string, init?: Parameters<typeof apiFetch>[1]): Promise<T> {
  try {
    return await apiFetch<T>(path, init);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) await expired();
    throw err;
  }
}

/**
 * Сессия больше не действует. В server action cookie стираем сразу и ведём на /login?expired=1:
 * редирект action через /auth/expired клиентский роутер проходит fetch-ем, и в адресной строке
 * осталось бы /auth/expired. В рендере страницы cookie менять нельзя — тогда через /auth/expired.
 */
async function expired(): Promise<never> {
  let cleared = false;
  try {
    await clearSession();
    cleared = true;
  } catch {
    // рендер серверного компонента: cookies() только для чтения
  }
  redirect(cleared ? "/login?expired=1" : "/auth/expired");
}
