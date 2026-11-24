"""Workflow state round-trips through the real Postgres checkpointer.

Simulates a process restart: pause at the approval gate with one graph
instance, then resume with a brand-new graph + saver connection.
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.types import Command

from app.database.checkpointer import open_checkpointer
from app.graph import nodes
from app.graph.builder import RECURSION_LIMIT, build_research_graph
from app.graph.state import initial_state
from tests.conftest import TEST_DB_URL
from tests.fakes import LIMITS, FakeLLM, make_context

pytestmark = pytest.mark.integration
PG_URL = TEST_DB_URL.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(nodes, "sleep", instant)


async def test_pause_restart_resume_with_postgres_checkpoints() -> None:
    thread = f"it-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread}, "recursion_limit": RECURSION_LIMIT}
    llm = FakeLLM()
    ctx, _ = make_context(llm)

    async with open_checkpointer(PG_URL) as saver:
        graph = build_research_graph(saver)
        first = await graph.ainvoke(
            initial_state(
                project_id="p",
                title="t",
                objective="Research Indian healthcare SaaS market",
                depth="quick",
                focus_areas=[],
                limits={**LIMITS, "require_plan_approval": True},
            ),
            config,
            context=ctx,
        )
        assert "__interrupt__" in first

    # "restart": new saver connection, new compiled graph, same thread id
    async with open_checkpointer(PG_URL) as saver2:
        graph2 = build_research_graph(saver2)
        snapshot = await graph2.aget_state(config)
        assert snapshot.next == ("approval_gate",)
        assert len(snapshot.values["tasks"]) == 3
        final = await graph2.ainvoke(Command(resume={"approved": True}), config, context=ctx)

        assert final["status"] == "completed", final.get("error")
        assert len(llm.calls("PlanOutput")) == 1
        assert final["report"]["markdown"].startswith("# t")
        # sources survived JSON checkpoint serialisation with their datetimes intact
        some = next(iter(final["sources"].values()))
        assert some["retrieved_at"] and some["citation_key"].startswith("S")
        history = [s async for s in graph2.aget_state_history(config)]
        assert len(history) > 5
