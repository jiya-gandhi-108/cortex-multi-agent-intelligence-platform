/**
 * Route guard: signed-out visitors are sent to /login; signed-in visitors skip the
 * auth pages. This only checks for the cookie; the backend validates the token on
 * every API call, and an invalid token is cleared by the API proxy (401).
 */
import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "cortex_session";
const AUTH_PAGES = ["/login", "/register"];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = Boolean(request.cookies.get(SESSION_COOKIE)?.value);

  if (AUTH_PAGES.includes(pathname)) {
    return signedIn ? NextResponse.redirect(new URL("/dashboard", request.url)) : NextResponse.next();
  }
  if (!signedIn) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/research/:path*", "/history/:path*", "/settings/:path*", "/login", "/register"],
};
