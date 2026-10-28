import { NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/session";

// Сюда ведёт requireMe() при 401: в рендере страницы cookie не стереть, поэтому стираем здесь.
// Location относительный: за standalone-сервером request.nextUrl.host = 0.0.0.0 (HOSTNAME), абсолютный адрес был бы неверным.
export function GET() {
  const res = new NextResponse(null, { status: 307, headers: { Location: "/login?expired=1" } });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
