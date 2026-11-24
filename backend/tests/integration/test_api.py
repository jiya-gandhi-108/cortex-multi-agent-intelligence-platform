"""API tests against a real Postgres database."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.rate_limit import InMemoryRateLimiter
from tests.conftest import auth, register

pytestmark = pytest.mark.integration

OBJECTIVE = (
    "Research the Indian healthcare SaaS market, identify major competitors, "
    "analyse pricing and recommend five business opportunities."
)


# --------------------------------------------------------------------------- auth


async def test_register_login_me_flow(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register", json={"email": "Alice@Example.com", "password": "s3cure-pass"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "alice@example.com"
    assert "hashed_password" not in body["user"]

    r = await client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": "s3cure-pass"}
    )
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = await client.get("/api/v1/auth/me", headers=auth(token))
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"
    assert r.headers["x-request-id"]


async def test_duplicate_registration_conflict(client: AsyncClient) -> None:
    await register(client, "dup@example.com")
    r = await client.post(
        "/api/v1/auth/register", json={"email": "DUP@example.com", "password": "an0ther-pass"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_login_wrong_password_and_unknown_user_look_identical(client: AsyncClient) -> None:
    await register(client, "bob@example.com")
    bad_pw = await client.post(
        "/api/v1/auth/login", json={"email": "bob@example.com", "password": "wrong-pass1"}
    )
    no_user = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass1"}
    )
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json()["error"] == no_user.json()["error"]


@pytest.mark.parametrize("header", [None, "Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "Bearer "])
async def test_me_rejects_missing_or_bad_tokens(client: AsyncClient, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    r = await client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_validation_error_envelope_does_not_echo_password(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": "SECRETVALUE"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "SECRETVALUE" not in r.text


async def test_auth_endpoints_rate_limited(db_engine: None) -> None:
    from httpx import ASGITransport

    from app.core.config import get_settings
    from app.main import create_app
    from tests.conftest import make_settings

    strict = make_settings(rate_limit_per_minute=30)  # auth limit = max(5, 30 // 6) = 5
    app = create_app(settings=strict, rate_limiter=InMemoryRateLimiter())
    app.dependency_overrides[get_settings] = lambda: strict
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        statuses = []
        for _ in range(7):
            r = await c.post(
                "/api/v1/auth/login", json={"email": "x@example.com", "password": "whatever1"}
            )
            statuses.append(r.status_code)
    assert statuses[:5] == [401] * 5
    assert statuses[5:] == [429, 429]
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) > 0


# ----------------------------------------------------------------------- projects


async def test_create_and_get_project(client: AsyncClient) -> None:
    token = await register(client, "carol@example.com")
    r = await client.post(
        "/api/v1/research",
        json={"objective": OBJECTIVE, "depth": "deep", "focus_areas": ["pricing", "competitors"]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "draft"
    assert p["depth"] == "deep"
    assert p["title"].startswith("Research the Indian healthcare SaaS market")
    assert p["config"]["max_tokens"] > 0
    assert p["has_report"] is False

    r = await client.get(f"/api/v1/research/{p['id']}", headers=auth(token))
    assert r.status_code == 200 and r.json()["objective"] == OBJECTIVE


async def test_list_projects_paginated_and_filtered(client: AsyncClient) -> None:
    token = await register(client, "dave@example.com")
    for i in range(5):
        await client.post(
            "/api/v1/research",
            json={"objective": f"{OBJECTIVE} variant {i}", "title": f"Project {i}"},
            headers=auth(token),
        )
    r = await client.get("/api/v1/research?limit=2&offset=1", headers=auth(token))
    page = r.json()
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 1
    assert [p["title"] for p in page["items"]] == ["Project 3", "Project 2"]  # newest first

    r = await client.get("/api/v1/research?status=completed", headers=auth(token))
    assert r.json()["total"] == 0

    r = await client.get("/api/v1/research?limit=1000", headers=auth(token))
    assert r.status_code == 422


async def test_user_cannot_see_or_list_other_users_projects(client: AsyncClient) -> None:
    owner = await register(client, "owner@example.com")
    intruder = await register(client, "intruder@example.com")
    r = await client.post("/api/v1/research", json={"objective": OBJECTIVE}, headers=auth(owner))
    project_id = r.json()["id"]

    r = await client.get(f"/api/v1/research/{project_id}", headers=auth(intruder))
    assert r.status_code == 404  # not 403: existence is not revealed
    r = await client.get(f"/api/v1/research/{uuid.uuid4()}", headers=auth(intruder))
    assert r.status_code == 404
    r = await client.get("/api/v1/research", headers=auth(intruder))
    assert r.json()["total"] == 0


async def test_project_limits_clamped_to_server_maximum(client: AsyncClient) -> None:
    token = await register(client, "erin@example.com")
    r = await client.post(
        "/api/v1/research",
        json={"objective": OBJECTIVE, "limits": {"max_tokens": 2_000_000, "max_cost_usd": 50}},
        headers=auth(token),
    )
    cfg = r.json()["config"]
    assert cfg["max_tokens"] == 120_000
    assert cfg["max_cost_usd"] == 5.0


# ----------------------------------------------------------------------- settings


async def test_settings_defaults_and_partial_update(client: AsyncClient) -> None:
    token = await register(client, "frank@example.com")
    r = await client.get("/api/v1/settings", headers=auth(token))
    s = r.json()
    assert s["preferred_provider"] == "groq"
    assert s["default_research_depth"] == "standard"
    providers = {p["provider"]: p for p in s["providers"]}
    assert set(providers) == {"groq", "gemini"}
    assert "api_key" not in r.text.lower()

    r = await client.put(
        "/api/v1/settings",
        json={"default_research_depth": "quick", "max_tokens_per_project": 30000},
        headers=auth(token),
    )
    assert r.status_code == 200
    s = r.json()
    assert s["default_research_depth"] == "quick"
    assert s["max_tokens_per_project"] == 30000
    assert s["preferred_provider"] == "groq"  # untouched

    # New projects inherit the tighter user limit and default depth.
    r = await client.post("/api/v1/research", json={"objective": OBJECTIVE}, headers=auth(token))
    assert r.json()["config"]["max_tokens"] == 30000
    assert r.json()["depth"] == "quick"


async def test_settings_rejects_invalid_values(client: AsyncClient) -> None:
    token = await register(client, "gina@example.com")
    r = await client.put(
        "/api/v1/settings", json={"preferred_provider": "openai"}, headers=auth(token)
    )
    assert r.status_code == 422
    r = await client.put(
        "/api/v1/settings", json={"max_research_iterations": 99}, headers=auth(token)
    )
    assert r.status_code == 422


# ------------------------------------------------------------------------- misc


async def test_health_endpoints(client: AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    r = await client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"] == "ok"
    assert set(body["integrations"]) == {"groq", "gemini", "tavily", "exa", "langsmith"}


async def test_oversized_body_rejected(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        content=b"x" * (300 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


async def test_openapi_documents_all_routes(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    for path in (
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/research",
        "/api/v1/research/{project_id}",
        "/api/v1/settings",
    ):
        assert path in paths


async def test_logout_revokes_token_server_side(db_engine: None) -> None:
    import os

    from httpx import ASGITransport
    from redis.asyncio import Redis

    from app.core.config import get_settings
    from app.main import create_app
    from tests.conftest import make_settings

    settings = make_settings()
    app = create_app(settings=settings, rate_limiter=InMemoryRateLimiter())
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/15"))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            token = await register(c, "revoke@example.com")
            assert (await c.get("/api/v1/auth/me", headers=auth(token))).status_code == 200
            assert (await c.post("/api/v1/auth/logout", headers=auth(token))).status_code == 204
            r = await c.get("/api/v1/auth/me", headers=auth(token))
            assert r.status_code == 401  # copied token no longer works
            # a fresh login still works
            r = await c.post(
                "/api/v1/auth/login",
                json={"email": "revoke@example.com", "password": "s3cure-pass"},
            )
            assert (
                await c.get("/api/v1/auth/me", headers=auth(r.json()["access_token"]))
            ).status_code == 200
    finally:
        await app.state.redis.aclose()
