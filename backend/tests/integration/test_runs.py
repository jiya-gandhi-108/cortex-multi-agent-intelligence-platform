"""Run lifecycle through the real API, run manager, Postgres checkpointer and Redis.

Only the LLM and the web are faked.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import update

from app.core.config import get_settings
from app.database.checkpointer import open_checkpointer
from app.database.session import session_scope
from app.graph import nodes
from app.models import ResearchProject
from app.models.enums import ProjectStatus
from app.services.runner import ResearchRunner
from tests.conftest import TEST_DB_URL, auth, make_settings, register
from tests.fakes import FakeCollector, FakeLLM

pytestmark = pytest.mark.integration
PG_URL = TEST_DB_URL.replace("postgresql+psycopg://", "postgresql://")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")
OBJECTIVE = (
    "Research the Indian healthcare SaaS market, its competitors and pricing, and opportunities."
)


class GatedCollector(FakeCollector):
    """Blocks inside evidence collection until released (to test mid-run control)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def collect(self, *args: Any, **kwargs: Any) -> Any:
        self.entered.set()
        await self.gate.wait()
        return await super().collect(*args, **kwargs)


class Harness:
    def __init__(self, client: AsyncClient, saver: Any, redis: Redis, settings: Any) -> None:
        self.client, self.saver, self.redis, self.settings = client, saver, redis, settings
        self.llm = FakeLLM()
        self.collector: FakeCollector = FakeCollector()
        self.runner = self.new_runner()

    def new_runner(self) -> ResearchRunner:
        runner = ResearchRunner(
            self.settings,
            self.redis,
            self.saver,
            llm_factory=lambda s, p: self.llm,
            collector_factory=lambda s: self.collector,
            cancel_grace_seconds=0.5,
        )
        self.client._transport.app.state.runner = runner  # type: ignore[attr-defined]
        self.runner = runner
        return runner

    async def project(self, token: str, **extra: Any) -> str:
        body = {"objective": OBJECTIVE, "depth": "quick", **extra}
        r = await self.client.post("/api/v1/research", json=body, headers=auth(token))
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    async def start_and_wait(self, token: str, pid: str) -> dict[str, Any]:
        r = await self.client.post(f"/api/v1/research/{pid}/start", headers=auth(token))
        assert r.status_code == 202, r.text
        await self.runner.wait(uuid.UUID(pid), 30)
        return (await self.client.get(f"/api/v1/research/{pid}/status", headers=auth(token))).json()


@pytest.fixture
async def h(
    db_engine: None, rate_limiter: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Harness]:
    from app.main import create_app

    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(nodes, "sleep", instant)
    settings = make_settings(
        groq_api_key="gsk_test",
        tavily_api_key="tvly-test",
        redis_url=REDIS_URL,
        max_active_runs_per_user=2,
    )
    app = create_app(settings=settings, rate_limiter=rate_limiter)
    app.dependency_overrides[get_settings] = lambda: settings
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.redis = redis
    async with open_checkpointer(PG_URL) as saver:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            harness = Harness(client, saver, redis, settings)
            yield harness
            await harness.runner.shutdown()
    await redis.flushdb()
    await redis.aclose()


# ---------------------------------------------------------------- happy path


async def test_full_run_persists_everything_and_serves_results(h: Harness) -> None:
    token = await register(h.client, "runner@example.com")
    pid = await h.project(token)
    status = await h.start_and_wait(token, pid)

    assert status["status"] == "completed", status["error_message"]
    assert status["run"] == 1
    assert status["tasks"] == {
        "total": 3,
        "pending": 0,
        "running": 0,
        "completed": 3,
        "failed": 0,
        "skipped": 0,
    }
    assert status["sources"] == 6
    assert status["total_tokens"] > 0
    assert status["plan"]["domain"] == "market_research"
    assert status["summary"]["verification"]["sufficient"] is True
    assert status["duration_seconds"] is not None

    hdr = auth(token)
    sources = (await h.client.get(f"/api/v1/research/{pid}/sources?limit=4", headers=hdr)).json()
    assert sources["total"] == 6 and len(sources["items"]) == 4
    first = sources["items"][0]
    assert first["citation_key"].startswith("S") and first["task_key"] in ("task_001", "task_002")
    assert first["fetched"] is True and first["excerpt"]

    agents = {
        a["agent_name"]: a
        for a in (await h.client.get(f"/api/v1/research/{pid}/agents", headers=hdr)).json()
    }
    assert set(agents) == {
        "planner_agent",
        "research_agent",
        "competitor_agent",
        "market_analysis_agent",
        "verification_agent",
        "report_agent",
    }
    assert all(a["failed"] == 0 and a["tokens"] > 0 for a in agents.values())

    execs = (
        await h.client.get(f"/api/v1/research/{pid}/executions?agent=research_agent", headers=hdr)
    ).json()
    assert execs["total"] == 1 and execs["items"][0]["output_data"]["sources_retrieved"] == 3

    tasks = (await h.client.get(f"/api/v1/research/{pid}/tasks", headers=hdr)).json()
    assert [t["task_key"] for t in tasks] == ["task_001", "task_002", "task_003"]

    report = (await h.client.get(f"/api/v1/research/{pid}/report", headers=hdr)).json()
    assert report["version"] == 1
    assert "## 1. Executive Summary" in report["report_content"]
    assert report["structured"]["quality"]["passed"] is True
    assert report["verification"]["evidence_score"] > 0

    md = await h.client.get(f"/api/v1/research/{pid}/report/download", headers=hdr)
    assert md.headers["content-type"].startswith("text/markdown")
    assert 'filename="cortex-research-the-indian-healthcare' in md.headers["content-disposition"]
    pdf = await h.client.get(f"/api/v1/research/{pid}/report/pdf", headers=hdr)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    dash = (await h.client.get("/api/v1/dashboard", headers=hdr)).json()
    assert (
        dash["total_projects"] == 1 and dash["completed_tasks"] == 3 and dash["total_reports"] == 1
    )
    assert dash["active_executions"] == 0 and dash["projects_by_status"] == {"completed": 1}
    assert dash["recent_reports"][0]["evidence_score"] is not None


async def test_event_stream_replays_finished_run(h: Harness) -> None:
    token = await register(h.client, "events@example.com")
    pid = await h.project(token)
    await h.start_and_wait(token, pid)
    r = await h.client.get(f"/api/v1/research/{pid}/events", headers=auth(token))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert (
        body.index("event: snapshot") < body.index("event: plan") < body.index("event: completed")
    )
    last_event = body.strip().split("event: ")[-1]
    assert (
        last_event.startswith("status") and '"completed"' in last_event
    )  # closes on committed status
    for kind in ("task_started", "tool_call", "llm_call", "verification", "report", "status"):
        assert f"event: {kind}" in body
    ids = [line[4:] for line in body.splitlines() if line.startswith("id: ")]
    assert len(ids) > 10

    resumed = await h.client.get(
        f"/api/v1/research/{pid}/events", headers={**auth(token), "Last-Event-ID": ids[-3]}
    )
    assert resumed.text.count("\nid: ") + resumed.text.startswith("id: ") <= 3


async def test_rerun_creates_new_run_and_report_version(h: Harness) -> None:
    token = await register(h.client, "rerun@example.com")
    pid = await h.project(token)
    await h.start_and_wait(token, pid)
    second = await h.start_and_wait(token, pid)
    assert second["status"] == "completed" and second["run"] == 2
    assert second["tasks"]["total"] == 3  # previous run's tasks replaced
    hdr = auth(token)
    assert (await h.client.get(f"/api/v1/research/{pid}/report", headers=hdr)).json()[
        "version"
    ] == 2
    assert (
        await h.client.get(f"/api/v1/research/{pid}/report?version=1", headers=hdr)
    ).status_code == 200
    execs = (await h.client.get(f"/api/v1/research/{pid}/executions", headers=hdr)).json()
    assert execs["total"] == 12  # execution history kept across runs (6 per run)


# ------------------------------------------------------------- run control


async def test_cancel_mid_run_and_double_start_rejected(h: Harness) -> None:
    gated = GatedCollector()
    h.collector = gated
    token = await register(h.client, "cancel@example.com")
    pid = await h.project(token)
    hdr = auth(token)
    assert (await h.client.post(f"/api/v1/research/{pid}/start", headers=hdr)).status_code == 202
    await asyncio.wait_for(gated.entered.wait(), 10)

    again = await h.client.post(f"/api/v1/research/{pid}/start", headers=hdr)
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"

    r = await h.client.post(f"/api/v1/research/{pid}/cancel", headers=hdr)
    assert r.status_code == 202
    await h.runner.wait(uuid.UUID(pid), 10)
    status = (await h.client.get(f"/api/v1/research/{pid}/status", headers=hdr)).json()
    assert status["status"] == "cancelled"
    assert status["plan"] is not None and status["tasks"]["total"] == 3  # progress preserved
    assert (await h.client.post(f"/api/v1/research/{pid}/cancel", headers=hdr)).status_code == 409


async def test_plan_approval_flow_and_rejection(h: Harness) -> None:
    token = await register(h.client, "approve@example.com")
    hdr = auth(token)
    pid = await h.project(token, limits={"require_plan_approval": True})
    paused = await h.start_and_wait(token, pid)
    assert paused["status"] == "awaiting_approval" and paused["awaiting_approval"] is True
    assert len(paused["plan"]["tasks"]) == 3
    assert not h.llm.calls("SearchQueries")

    r = await h.client.post(f"/api/v1/research/{pid}/approve", json={"approved": True}, headers=hdr)
    assert r.status_code == 202
    await h.runner.wait(uuid.UUID(pid), 30)
    done = (await h.client.get(f"/api/v1/research/{pid}/status", headers=hdr)).json()
    assert done["status"] == "completed"
    assert len(h.llm.calls("PlanOutput")) == 1

    pid2 = await h.project(token, limits={"require_plan_approval": True})
    await h.start_and_wait(token, pid2)
    await h.client.post(
        f"/api/v1/research/{pid2}/approve",
        json={"approved": False, "reason": "too broad"},
        headers=hdr,
    )
    await h.runner.wait(uuid.UUID(pid2), 30)
    rejected = (await h.client.get(f"/api/v1/research/{pid2}/status", headers=hdr)).json()
    assert rejected["status"] == "cancelled" and "too broad" in rejected["error_message"]
    bad = await h.client.post(
        f"/api/v1/research/{pid2}/approve", json={"approved": True}, headers=hdr
    )
    assert bad.status_code == 409


async def test_shutdown_interrupts_and_restart_resumes_from_checkpoint(h: Harness) -> None:
    gated = GatedCollector()
    h.collector = gated
    token = await register(h.client, "resume@example.com")
    hdr = auth(token)
    pid = await h.project(token)
    await h.client.post(f"/api/v1/research/{pid}/start", headers=hdr)
    await asyncio.wait_for(gated.entered.wait(), 10)

    await h.runner.shutdown()  # simulated deploy/restart mid-run
    status = (await h.client.get(f"/api/v1/research/{pid}/status", headers=hdr)).json()
    assert status["status"] == "interrupted"

    h.collector = FakeCollector()
    h.new_runner()  # fresh process, same checkpoint store
    resumed = await h.start_and_wait(token, pid)
    assert resumed["status"] == "completed", resumed["error_message"]
    assert resumed["run"] == 1  # resumed, not re-run
    assert len(h.llm.calls("PlanOutput")) == 1  # planning was not repeated


async def test_orphaned_active_runs_marked_interrupted_on_startup(h: Harness) -> None:
    token = await register(h.client, "orphan@example.com")
    pid = await h.project(token)
    async with session_scope() as s:
        await s.execute(
            update(ResearchProject)
            .where(ResearchProject.id == uuid.UUID(pid))
            .values(status=ProjectStatus.RUNNING)
        )
    assert uuid.UUID(pid) in await h.runner.recover_orphans()
    status = (await h.client.get(f"/api/v1/research/{pid}/status", headers=auth(token))).json()
    assert status["status"] == "interrupted" and "restart" in status["error_message"]


async def test_per_user_active_run_limit(h: Harness) -> None:
    gated = GatedCollector()
    h.collector = gated
    h.settings.max_active_runs_per_user = 1
    token = await register(h.client, "limit@example.com")
    hdr = auth(token)
    a, b = await h.project(token), await h.project(token)
    assert (await h.client.post(f"/api/v1/research/{a}/start", headers=hdr)).status_code == 202
    r = await h.client.post(f"/api/v1/research/{b}/start", headers=hdr)
    assert r.status_code == 409 and "active runs" in r.json()["error"]["message"]
    gated.gate.set()
    await h.runner.wait(uuid.UUID(a), 30)


async def test_start_without_integrations_is_a_clear_503(h: Harness) -> None:
    h.settings.groq_api_key = None
    h.settings.tavily_api_key = None
    token = await register(h.client, "nokeys@example.com")
    pid = await h.project(token)
    r = await h.client.post(f"/api/v1/research/{pid}/start", headers=auth(token))
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["code"] == "integration_not_configured"
    assert body["details"]["missing"] == ["GROQ_API_KEY", "TAVILY_API_KEY"]


async def test_failed_run_status_and_no_report(h: Harness) -> None:
    h.llm = FakeLLM({"PlanOutput": lambda r: {"bad": "shape"}})
    token = await register(h.client, "fail@example.com")
    pid = await h.project(token)
    status = await h.start_and_wait(token, pid)
    assert status["status"] == "failed"
    assert status["error_message"].startswith("planning_failed")
    r = await h.client.get(f"/api/v1/research/{pid}/report", headers=auth(token))
    assert r.status_code == 404


# --------------------------------------------------------------- isolation


async def test_other_users_cannot_touch_runs(h: Harness) -> None:
    owner = await register(h.client, "own@example.com")
    other = await register(h.client, "other@example.com")
    pid = await h.project(owner)
    await h.start_and_wait(owner, pid)
    for method, path in [
        ("post", "start"),
        ("post", "cancel"),
        ("get", "status"),
        ("get", "tasks"),
        ("get", "agents"),
        ("get", "executions"),
        ("get", "sources"),
        ("get", "events"),
        ("get", "report"),
        ("get", "report/pdf"),
        ("get", "report/download"),
    ]:
        r = await getattr(h.client, method)(f"/api/v1/research/{pid}/{path}", headers=auth(other))
        assert r.status_code == 404, (path, r.status_code)
    dash = (await h.client.get("/api/v1/dashboard", headers=auth(other))).json()
    assert dash["total_projects"] == 0 and dash["completed_tasks"] == 0
