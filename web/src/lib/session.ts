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
 * 401 (сессия истекла/отозвана) → cookie стирается и редирект. Другие ошибки API пробрасываются.
 */
export async function requireMe(): Promise<Me> {
  try {
    return await apiFetch<Me>("/v1/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      try {
        await clearSession();
      } catch {
        // вызвано при рендере страницы: cookie менять нельзя; /login с протухшим cookie всё равно открывается
      }
      redirect("/login");
    }
    throw err;
  }
}
