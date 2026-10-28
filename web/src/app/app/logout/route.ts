import { NextResponse } from "next/server";

import { apiFetchRaw } from "@/lib/api";
import { SESSION_COOKIE } from "@/lib/session";

/** Выход: отзываем сессию в API (ошибки не важны), стираем cookie, 303 на /login (Location относительный, см. /auth/expired). */
export async function POST() {
  try {
    await apiFetchRaw("/v1/auth/logout", { method: "POST" });
  } catch (e) {
    console.error("выход: API недоступен, cookie всё равно стираем", e);
  }
  const res = new NextResponse(null, { status: 303, headers: { Location: "/login" } });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
