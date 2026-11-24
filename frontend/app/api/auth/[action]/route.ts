/**
 * Session endpoints: POST /api/auth/login | /api/auth/register | /api/auth/logout.
 * Login/register exchange credentials with the backend and store the JWT in an
 * httpOnly cookie; the token itself is never returned to the browser.
 */
import { NextRequest, NextResponse } from "next/server";

import {
  SESSION_COOKIE,
  backendUrl,
  clientHeaders,
  errorResponse,
  isSameOrigin,
  sessionCookieOptions,
} from "@/lib/server/backend";

interface TokenBody {
  access_token: string;
  expires_in: number;
}

async function backendJson(req: NextRequest, path: string, body: unknown) {
  return fetch(`${backendUrl()}/api/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...clientHeaders(req) },
    body: JSON.stringify(body),
    cache: "no-store",
  });
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (!isSameOrigin(req)) return errorResponse(403, "forbidden", "Cross-origin request rejected");

  if (action === "logout") {
    // Revoke server-side first, so a copied token stops working too.
    const token = req.cookies.get(SESSION_COOKIE)?.value;
    if (token) {
      await fetch(`${backendUrl()}/api/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      }).catch(() => undefined);
    }
    const res = NextResponse.json({ ok: true });
    res.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(0) });
    return res;
  }
  if (action !== "login" && action !== "register") {
    return errorResponse(404, "not_found", "Unknown auth action");
  }

  let credentials: unknown;
  try {
    credentials = await req.json();
  } catch {
    return errorResponse(400, "bad_request", "Expected a JSON body");
  }

  let upstream: Response;
  try {
    upstream = await backendJson(req, `/auth/${action}`, credentials);
  } catch {
    return errorResponse(502, "backend_unreachable", "The Cortex API is not reachable right now");
  }
  const data = await upstream.json().catch(() => null);
  if (!upstream.ok || !data) {
    return NextResponse.json(data ?? { error: { code: "error", message: "Request failed" } }, {
      status: upstream.status,
    });
  }

  const token: TokenBody = action === "register" ? data.token : data;
  const meRes = await fetch(`${backendUrl()}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token.access_token}` },
    cache: "no-store",
  });
  const user = action === "register" ? data.user : await meRes.json();

  const res = NextResponse.json({ user }, { status: action === "register" ? 201 : 200 });
  res.cookies.set(SESSION_COOKIE, token.access_token, sessionCookieOptions(token.expires_in));
  return res;
}
