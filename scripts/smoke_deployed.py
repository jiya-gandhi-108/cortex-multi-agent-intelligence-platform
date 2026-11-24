"""Post-deploy smoke test: exercises a deployed Cortex the way a browser does.

Everything goes through the frontend (Vercel) URL, so it also proves the BFF proxy,
the proxy shared secret, CORS-free same-origin calls and cookie handling. Creates one
throwaway account.

    python scripts/smoke_deployed.py https://cortex.vercel.app --api https://cortex-api.onrender.com
    python scripts/smoke_deployed.py https://cortex.vercel.app --full     # also runs one quick
                                                                          # research (spends Groq
                                                                          # and Tavily credits)

Exit code 0 means every check passed. Only needs httpx (the backend venv has it).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

RESULTS: list[tuple[str, bool, str]] = []
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)
    return ok


def step(name: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:  # report and keep going; the summary decides the exit code
        check(name, False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("frontend", help="Frontend base URL, e.g. https://cortex.vercel.app")
    parser.add_argument("--api", help="Backend base URL (checks health directly; optional)")
    parser.add_argument("--full", action="store_true", help="Run one quick research end to end")
    parser.add_argument("--timeout", type=float, default=900, help="Seconds to wait for --full")
    args = parser.parse_args()

    base = args.frontend.rstrip("/")
    https = urlparse(base).scheme == "https"
    # Render's free tier sleeps; the first request can take about a minute.
    client = httpx.Client(
        base_url=base, timeout=httpx.Timeout(90, connect=30), follow_redirects=False
    )
    origin = {"Origin": base}
    state: dict[str, str] = {}

    print(f"Smoke testing {base}")

    if args.api:

        def api_health() -> None:
            api = args.api.rstrip("/")
            r = httpx.get(f"{api}/health/ready", timeout=120)
            body = r.json()
            check(
                "backend /health/ready", r.status_code == 200, json.dumps(body.get("checks", body))
            )
            r = httpx.get(f"{api}/api/v1/research", timeout=30)
            check(
                "backend rejects unauthenticated API calls",
                r.status_code == 401,
                str(r.status_code),
            )

        step("backend health", api_health)

    def landing() -> None:
        r = client.get("/")
        check(
            "landing page renders", r.status_code == 200 and "Cortex" in r.text, str(r.status_code)
        )
        h = r.headers
        csp = h.get("content-security-policy", "")
        check("CSP header present", "default-src 'self'" in csp)
        check(
            "frame-ancestors blocked",
            "frame-ancestors 'none'" in csp or h.get("x-frame-options") == "DENY",
        )
        check("nosniff header", h.get("x-content-type-options") == "nosniff")
        if https:
            check("HSTS header", "max-age" in h.get("strict-transport-security", ""))

    step("landing", landing)

    def protected_redirect() -> None:
        r = client.get("/dashboard")
        check(
            "signed-out /dashboard redirects to login",
            r.status_code in (307, 308) and "/login" in r.headers.get("location", ""),
        )
        r = client.get("/api/backend/research")
        check("signed-out API proxy returns 401", r.status_code == 401, str(r.status_code))

    step("auth gate", protected_redirect)

    email = f"smoke-{secrets.token_hex(4)}@example.com"
    password = f"Smoke-{secrets.token_urlsafe(12)}9"

    def register() -> None:
        r = client.post(
            "/api/auth/register", json={"email": email, "password": password}, headers=origin
        )
        check("register through frontend", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
        cookie = r.headers.get("set-cookie", "")
        check("session cookie is HttpOnly", "httponly" in cookie.lower())
        check("session cookie is SameSite=Lax", "samesite=lax" in cookie.lower())
        if https:
            check("session cookie is Secure", "secure" in cookie.lower())
        check("token not exposed in body", "access_token" not in r.text)
        if not https and r.status_code == 201:
            # Browsers treat http://localhost as secure and send Secure cookies; httpx does
            # not, so re-add the session cookie without the flag for local runs.
            token = cookie.split(";", 1)[0].removeprefix("cortex_session=")
            client.headers["Cookie"] = f"cortex_session={token}"
            state["token"] = token

    step("register", register)

    def cross_origin_rejected() -> None:
        r = client.post(
            "/api/backend/research",
            json={"objective": "x" * 30},
            headers={"Origin": "https://evil.example"},
        )
        check("cross-origin write rejected", r.status_code == 403, str(r.status_code))

    step("csrf", cross_origin_rejected)

    def create_project() -> None:
        body = {
            "objective": (
                "Assess the market for open-source vector databases "
                "for small engineering teams in 2026."
            ),
            "depth": "quick",
            "limits": {"require_plan_approval": False, "max_research_iterations": 0},
        }
        r = client.post("/api/backend/research", json=body, headers=origin)
        ok = check(
            "create research project", r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        )
        if ok:
            state["id"] = r.json()["id"]
        r = client.get("/api/backend/settings")
        if check("read settings", r.status_code == 200, str(r.status_code)):
            providers = {p["provider"]: p["configured"] for p in r.json().get("providers", [])}
            check(
                "Groq key configured on the backend",
                providers.get("groq") is True,
                json.dumps(providers),
            )

    step("project", create_project)

    def sse_snapshot() -> None:
        pid = state["id"]
        with client.stream(
            "GET", f"/api/backend/research/{pid}/events", headers={"Accept": "text/event-stream"}
        ) as r:
            ok = r.status_code == 200 and r.headers.get("content-type", "").startswith(
                "text/event-stream"
            )
            first = ""
            for line in r.iter_lines():
                if line.startswith("event:"):
                    first = line.split(":", 1)[1].strip()
                    break
        check(
            "event stream opens through the proxy",
            ok and first == "snapshot",
            f"first event: {first or 'none'}",
        )

    step("sse", sse_snapshot)

    if args.full:

        def full_run() -> None:
            pid = state["id"]
            r = client.post(f"/api/backend/research/{pid}/start", headers=origin)
            if not check("start research", r.status_code == 202, f"{r.status_code} {r.text[:200]}"):
                return
            deadline = time.monotonic() + args.timeout
            status = ""
            while time.monotonic() < deadline:
                s = client.get(f"/api/backend/research/{pid}/status").json()
                if s["status"] != status:
                    status = s["status"]
                    print(f"        status: {status} {s.get('stage') or ''}", flush=True)
                if status in TERMINAL:
                    break
                time.sleep(5)
            check("research completes", status == "completed", status or "timeout")
            if status != "completed":
                return
            report = client.get(f"/api/backend/research/{pid}/report").json()
            check(
                "report has citations",
                "[S" in report.get("report_content", ""),
                f"verification {(report.get('verification') or {}).get('score')}",
            )
            pdf = client.get(f"/api/backend/research/{pid}/report/pdf")
            check(
                "PDF export",
                pdf.status_code == 200 and pdf.content[:4] == b"%PDF",
                f"{len(pdf.content)} bytes",
            )

        step("full run", full_run)

    def logout() -> None:
        old_token = state.get("token") or client.cookies.get("cortex_session", "")
        r = client.post("/api/auth/logout", headers=origin)
        check("logout", r.status_code in (200, 204), str(r.status_code))
        client.cookies.clear()
        client.headers.pop("Cookie", None)
        r = client.get("/api/backend/research")
        check("session gone after logout", r.status_code == 401, str(r.status_code))
        if old_token:
            r = client.get(
                "/api/backend/research", headers={"Cookie": f"cortex_session={old_token}"}
            )
            check("old token revoked server-side", r.status_code == 401, str(r.status_code))

    step("logout", logout)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("Failed:", ", ".join(failed))
    print(f"Throwaway account: {email}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
