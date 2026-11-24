"""End-to-end LangGraph workflow tests with fake LLM and collector (InMemorySaver)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.base import WorkflowCancelledError
from app.graph import nodes
from app.graph.builder import RECURSION_LIMIT, build_research_graph
from app.graph.events import EventType
from app.graph.state import initial_state
from app.llm.base import LLMTransientError
from app.tools.search import SearchConfigurationError
from tests.fakes import (
    LIMITS,
    FakeCollector,
    FakeLLM,
    make_context,
    verify_all_supported,
    verify_all_unsupported,
)

OBJECTIVE = "Research the Indian healthcare SaaS market and recommend opportunities."


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(nodes, "sleep", instant)


async def run(
    ctx,
    *,
    limits: dict[str, Any] | None = None,
    saver=None,
    thread: str | None = None,
    resume: Any = None,
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    graph = build_research_graph(saver or InMemorySaver())
    config = {
        "configurable": {"thread_id": thread or uuid.uuid4().hex},
        "recursion_limit": RECURSION_LIMIT,
    }
    payload: Any = (
        Command(resume=resume)
        if resume is not None
        else initial_state(
            project_id="proj-1",
            title="Healthcare SaaS India",
            objective=OBJECTIVE,
            depth="quick",
            focus_areas=["competitors"],
            limits={**LIMITS, **(limits or {})},
        )
    )
    result = await graph.ainvoke(payload, config, context=ctx)
    return result, graph, config


async def test_happy_path_runs_all_agents_and_produces_cited_report() -> None:
    llm = FakeLLM()
    ctx, sink = make_context(llm)
    state, _, _ = await run(ctx)

    assert state["status"] == "completed", state.get("error")
    assert {k: t["status"] for k, t in state["tasks"].items()} == {
        "task_001": "completed",
        "task_002": "completed",
        "task_003": "completed",
    }

    # research + competitor ran in parallel; market only after both finished
    order = [
        (e.type, e.task_key)
        for e in sink.events
        if e.type in (EventType.TASK_STARTED, EventType.TASK_COMPLETED)
    ]
    started_2 = order.index((EventType.TASK_STARTED, "task_002"))
    done_1 = order.index((EventType.TASK_COMPLETED, "task_001"))
    assert started_2 < done_1
    market_start = order.index((EventType.TASK_STARTED, "task_003"))
    assert market_start > order.index((EventType.TASK_COMPLETED, "task_002"))

    report = state["report"]
    md = report["markdown"]
    assert "Sdeadbe" not in md
    assert report["validation"]["invalid_citations_removed"] >= 1
    assert set(report["cited_sources"]) <= set(state["sources"])
    assert "## 11. Research Limitations" in md
    assert state["verification"]["sufficient"] is True
    assert state["quality"]["passed"] is True

    agents = {e["agent"] for e in state["executions"]}
    assert agents == {
        "planner_agent",
        "research_agent",
        "competitor_agent",
        "market_analysis_agent",
        "verification_agent",
        "report_agent",
    }
    assert state["usage"]["prompt_tokens"] == sum(e["prompt_tokens"] for e in state["executions"])
    assert (
        ctx.budget.used.total_tokens
        == state["usage"]["prompt_tokens"] + state["usage"]["completion_tokens"]
    )
    assert sink.of(EventType.COMPLETED)


async def test_failed_agent_is_contained_dependants_skipped_results_kept() -> None:
    llm = FakeLLM({"CompetitorOutput": lambda r: LLMTransientError("provider down")})
    ctx, sink = make_context(llm)
    state, _, _ = await run(ctx, limits={"max_agent_retries": 2})

    tasks = state["tasks"]
    assert tasks["task_001"]["status"] == "completed"
    assert tasks["task_002"]["status"] == "failed" and tasks["task_002"]["attempts"] == 3
    assert tasks["task_003"]["status"] == "skipped"
    assert len(sink.of(EventType.TASK_RETRY)) == 2
    assert state["status"] == "completed"  # report built from surviving evidence
    assert any("incomplete tasks" in w for w in state["quality"]["warnings"])
    assert "Tasks not completed" in state["report"]["markdown"]
    failed_execs = [e for e in state["executions"] if e["agent"] == "competitor_agent"]
    assert [e["status"] for e in failed_execs] == ["failed"] * 3


async def test_budget_exhaustion_is_an_explicit_error_state() -> None:
    llm = FakeLLM(tokens=(4000, 1000))
    ctx, sink = make_context(llm, max_tokens=12_000)
    state, _, _ = await run(ctx)
    assert state["status"] == "failed"
    assert state["error"]["code"] == "budget_exceeded"
    assert state.get("report") is None  # no partial report
    assert state["plan"] is not None  # completed work preserved
    assert sink.of(EventType.ERROR)


async def test_insufficient_evidence_triggers_bounded_followup_research() -> None:
    llm = FakeLLM({"VerificationBatchOutput": verify_all_unsupported})
    collector = FakeCollector()
    ctx, _ = make_context(llm, collector)
    state, _, _ = await run(ctx, limits={"max_research_iterations": 1})

    assert state["iteration"] == 1
    followups = [k for k, t in state["tasks"].items() if t["iteration"] == 1]
    assert followups == ["task_101"]
    assert (
        "task_101",
        ["india healthcare saas revenue 2025"],
    ) in collector.calls  # seed queries used
    assert state["status"] == "failed"
    assert state["error"]["code"] == "insufficient_evidence"


async def test_followup_round_can_rescue_the_run() -> None:
    rounds = {"n": 0}

    def verifier(req):
        rounds["n"] += 1
        return verify_all_unsupported(req) if rounds["n"] == 1 else verify_all_supported(req)

    ctx, _ = make_context(FakeLLM({"VerificationBatchOutput": verifier}))
    state, _, _ = await run(ctx, limits={"max_research_iterations": 3})
    # Round-1 claims all failed; follow-up evidence is judged on its own and rescues the
    # run (each follow-up yields 2 verified claims, so two rounds reach the 3-claim minimum).
    assert state["status"] == "completed", state.get("error")
    assert state["verification"]["sufficient"] is True
    assert 1 <= state["iteration"] <= 3
    assert state["verification"]["counts"]["unsupported"] > 0  # earlier failures still recorded
    assert "unsupported" in state["report"]["markdown"]


async def test_zero_iterations_goes_straight_to_terminal_decision() -> None:
    ctx, _ = make_context(FakeLLM({"VerificationBatchOutput": verify_all_unsupported}))
    state, _, _ = await run(ctx, limits={"max_research_iterations": 0})
    assert state["iteration"] == 0
    assert state["error"]["code"] == "insufficient_evidence"


async def test_human_approval_pauses_before_any_research_and_resumes() -> None:
    llm = FakeLLM()
    ctx, sink = make_context(llm)
    saver, thread = InMemorySaver(), "approval-thread"
    state, graph, config = await run(
        ctx, limits={"require_plan_approval": True}, saver=saver, thread=thread
    )

    assert "__interrupt__" in state
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("approval_gate",)
    assert not llm.calls("SearchQueries")  # nothing paid for beyond planning
    assert sink.of(EventType.APPROVAL_REQUIRED)

    state, _, _ = await run(ctx, saver=saver, thread=thread, resume={"approved": True})
    assert state["status"] == "completed"
    assert len(llm.calls("PlanOutput")) == 1  # resume did not re-run the planner


async def test_rejected_plan_cancels_cleanly() -> None:
    ctx, _ = make_context()
    saver, thread = InMemorySaver(), "reject-thread"
    await run(ctx, limits={"require_plan_approval": True}, saver=saver, thread=thread)
    state, _, _ = await run(
        ctx, saver=saver, thread=thread, resume={"approved": False, "reason": "too broad"}
    )
    assert state["status"] == "cancelled"
    assert state["error"] == {"code": "plan_rejected", "message": "too broad"}


async def test_cancellation_stops_at_next_boundary_and_checkpoint_keeps_progress() -> None:
    ctx, _ = make_context()
    collector = FakeCollector(on_collect=lambda key: ctx.cancel_event.set())
    ctx.collector = collector  # type: ignore[assignment]
    saver = InMemorySaver()
    with pytest.raises(WorkflowCancelledError):
        await run(ctx, saver=saver, thread="cancel-thread")
    graph = build_research_graph(saver)
    snap = await graph.aget_state({"configurable": {"thread_id": "cancel-thread"}})
    assert snap.values["plan"] is not None
    assert not any(
        t["status"] == "completed" and t["agent"] == "market_analysis_agent"
        for t in snap.values["tasks"].values()
    )


async def test_configuration_error_is_fatal_and_not_retried() -> None:
    class Unconfigured(FakeCollector):
        async def collect(self, *a, **k):
            raise SearchConfigurationError("tavily is not configured: set TAVILY_API_KEY")

    collector = Unconfigured()
    ctx, sink = make_context(FakeLLM(), collector)
    state, _, _ = await run(ctx, limits={"max_agent_retries": 3})
    assert state["status"] == "failed"
    assert state["error"]["code"] == "configuration_error"
    assert "TAVILY_API_KEY" in state["error"]["message"]
    assert not sink.of(EventType.TASK_RETRY)


async def test_planner_failure_ends_run_with_clear_error() -> None:
    bad = {
        "objective": "x" * 20,
        "domain": "market_research",
        "requirements": ["r"],
        "tasks": [
            {
                "task_id": "task_001",
                "agent": "market_analysis_agent",
                "priority": "high",
                "description": "analysis with nothing to analyse",
                "dependencies": [],
            }
        ],
    }
    ctx, _ = make_context(FakeLLM({"PlanOutput": lambda r: bad}))
    state, _, _ = await run(ctx, limits={"max_agent_retries": 0})
    assert state["status"] == "failed"
    assert state["error"]["code"] == "planning_failed"
    assert state["tasks"] == {}


async def test_report_quality_failure_retries_once_then_fails() -> None:
    def empty_core(req):
        return {
            "executive_summary": "x" * 60,
            "key_findings": [{"text": "no cites here", "citations": []}],
            "market_analysis": "m" * 30,
            "competitor_analysis": "c" * 30,
        }

    llm = FakeLLM({"ReportCore": empty_core})
    ctx, _ = make_context(llm)
    state, _, _ = await run(ctx)
    assert state["report_attempts"] == 2
    assert state["status"] == "failed"
    assert state["error"]["code"] == "quality_check_failed"
    assert state["report"] is not None  # kept for inspection, flagged by status
