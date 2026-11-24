/**
 * Server-only helpers for the backend-for-frontend layer.
 *
 * The browser never sees the JWT: it lives in an httpOnly cookie and these route
 * handlers attach it as a bearer token when forwarding to the FastAPI backend.
 */
import type { NextRequest } from "next/server";

export const SESSION_COOKIE = "cortex_session";

export function backendUrl(): string {
  return (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

/** Headers that tell the backend who the real client is (trusted via shared secret). */
export function clientHeaders(req: NextRequest): Record<string, string> {
  const headers: Record<string, string> = {};
  const secret = process.env.PROXY_SHARED_SECRET;
  const forwarded = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const ip = forwarded || req.headers.get("x-real-ip") || "";
  if (secret && ip) {
    headers["X-Client-IP"] = ip;
    headers["X-Proxy-Secret"] = secret;
  }
  return headers;
}

export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

/** Same-origin check for state-changing requests (defence in depth over SameSite=Lax). */
export function isSameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return true; // same-origin fetches from older browsers / server calls
  try {
    return new URL(origin).host === req.headers.get("host");
  } catch {
    return false;
  }
}

export function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}
