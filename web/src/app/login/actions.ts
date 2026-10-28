"use server";

import { redirect } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";
import { safeNext } from "@/lib/next-path";
import { setSession } from "@/lib/session";
import type { LoginResult } from "@/lib/types";

export type LoginState = { error?: string; email?: string };

const BAD = "Неверный email или пароль";
const FAILED = "Не получилось войти: сервис недоступен. Попробуйте ещё раз.";

/** Вход по email и паролю; успех → cookie сессии и редирект на `next` (только внутри /app). */
export async function login(_prev: LoginState, form: FormData): Promise<LoginState> {
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!email || !password) return { error: "Введите email и пароль", email };

  let result: LoginResult;
  try {
    result = await apiFetch<LoginResult>("/v1/auth/login", {
      method: "POST",
      auth: false,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return { error: BAD, email };
    if (e instanceof ApiError && e.status === 429) return { error: e.detail, email };
    console.error("вход не выполнен", e);
    return { error: FAILED, email };
  }
  await setSession(result.token, result.expires_at);
  redirect(safeNext(form.get("next")));
}
