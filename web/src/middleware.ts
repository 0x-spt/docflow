import { NextResponse, type NextRequest } from "next/server";

// Только быстрая проверка наличия cookie (без запроса в API); недействительную сессию ловит requireMe().
const SESSION_COOKIE = "df_session";

export function middleware(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const url = request.nextUrl.clone();
  const next = request.nextUrl.pathname + request.nextUrl.search;
  url.pathname = "/login";
  url.search = "";
  url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/app/:path*"] };
