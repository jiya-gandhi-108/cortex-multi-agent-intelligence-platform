/**
 * Authenticated pass-through to the FastAPI backend: /api/backend/<path> -> {BACKEND_URL}/api/v1/<path>.
 * Streams responses unchanged, so SSE event streams and PDF downloads work through it.
 */
import { NextRequest } from "next/server";

import {
  SESSION_COOKIE,
  backendUrl,
  clientHeaders,
  errorResponse,
  isSameOrigin,
} from "@/lib/server/backend";

const PASS_REQUEST = ["content-type", "accept", "last-event-id", "x-request-id"];
const PASS_RESPONSE = [
  "content-type",
  "content-disposition",
  "x-request-id",
  "retry-after",
  "x-ratelimit-limit",
  "x-ratelimit-remaining",
];
const SEGMENT = /^[A-Za-z0-9._~-]+$/;

// Live run streams are long-lived; Vercel Hobby caps a function at 300 s. The client
// reconnects with Last-Event-ID, so a cut stream resumes without losing events.
export const maxDuration = 300;

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (!path.length || path.some((p) => !SEGMENT.test(p) || p === "." || p === "..")) {
    return errorResponse(400, "bad_request", "Invalid API path");
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return errorResponse(401, "unauthorized", "Sign in to continue");
  const mutating = !["GET", "HEAD"].includes(req.method);
  if (mutating && !isSameOrigin(req)) {
    return errorResponse(403, "forbidden", "Cross-origin request rejected");
  }

  const headers = new Headers({ Authorization: `Bearer ${token}`, ...clientHeaders(req) });
  for (const name of PASS_REQUEST) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${backendUrl()}/api/v1/${path.join("/")}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body: mutating ? await req.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: req.signal,
    });
  } catch {
    return errorResponse(502, "backend_unreachable", "The Cortex API is not reachable right now");
  }

  const out = new Headers({ "Cache-Control": "no-store" });
  for (const name of PASS_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  if (out.get("content-type")?.startsWith("text/event-stream")) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("X-Accel-Buffering", "no");
  }
  const response = new Response(upstream.body, { status: upstream.status, headers: out });
  if (upstream.status === 401) {
    response.headers.append("Set-Cookie", `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`);
  }
  return response;
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
