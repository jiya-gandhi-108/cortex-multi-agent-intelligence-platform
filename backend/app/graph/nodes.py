"""LangGraph nodes and routers for the research workflow.

Failure policy
  * Agent task failures are contained: after bounded retries the task is marked
    ``failed`` and dependants ``skipped``; completed results are kept.
  * Budget exhaustion, missing configuration and failures of the planner,
    verifier or report writer are *fatal*: the run ends in an explicit error
    state (``finalize``) instead of producing a partial report.
  * Cancellation raises ``WorkflowCancelledError`` at the next node boundary or
    LLM call; checkpointed state retains everything completed so far.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt

from app.agents.base import (
    AgentOutputError,
    CallLog,
    PromptTooLargeError,
    RunContext,
    WorkflowCancelledError,
    now_iso,
)
from app.agents.market import run_market_analysis
from app.agents.planner import run_planner
from app.agents.profiles import DepthProfile, profile_for
from app.agents.report import (
    build_digest,
    cited_keys,
    evidence_limitations,
    quality_check,
    render_markdown,
    validate_report,
    write_report,
)
from app.agents.research import AgentResult, run_competitor, run_research
from app.agents.verification import build_claims, score_verification, verify_claims
from app.core.logging import get_logger
from app.graph.events import EventType
from app.graph.state import ResearchState, TaskPayload, load_claims, load_sources
from app.llm.base import LLMConfigurationError, LLMError
from app.llm.retry import backoff_delay
from app.monitoring.cost import BudgetExceededError
from app.schemas.agents import ReportCore, ReportStrategy
from app.schemas.evidence import SourceRecord
from app.tools.search import SearchConfigurationError, SearchError

log = get_logger(__name__)

RESEARCH, COMPETITOR, MARKET = "research_agent", "competitor_agent", "market_analysis_agent"
AGENT_NODES = (RESEARCH, COMPETITOR, MARKET)
MAX_REPORT_ATTEMPTS = 2
MAX_FOLLOWUP_TASKS = 2
DONE = {"completed"}
DEAD = {"failed", "skipped", "cancelled"}

# Patchable in tests; looked up at call time.
sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


# ------------------------------------------------------------ execution log


def _execution(
    agent: str,
    log_: CallLog,
    *,
    task_key: str | None,
    attempt: int,
    status: str,
    started: str,
    t0: float,
    error: str | None = None,
    output: dict[str, Any] | None = None,
    input_: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "agent": agent,
        "task_key": task_key,
        "attempt": attempt,
        "status": status,
        "started_at": started,
        "finished_at": now_iso(),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "error": error,
        "input": input_ or {},
        "output": output or {},
        **log_.as_dict(),
    }


def _usage(executions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "prompt_tokens": sum(e["prompt_tokens"] for e in executions),
        "completion_tokens": sum(e["completion_tokens"] for e in executions),
        "cost_usd": round(sum(float(e["cost_usd"]) for e in executions), 6),
        "llm_calls": sum(e["llm_calls"] for e in executions),
    }


Classified = tuple[bool, dict[str, Any] | None]  # (retryable, fatal_error)


def classify(exc: Exception) -> Classified:
    if isinstance(exc, BudgetExceededError):
        return False, {"code": "budget_exceeded", "message": str(exc)}
    if isinstance(exc, (LLMConfigurationError, SearchConfigurationError)):
        return False, {"code": "configuration_error", "message": str(exc)}
    if isinstance(exc, PromptTooLargeError):
        return False, {"code": "internal_error", "message": str(exc)}
    if isinstance(exc, (LLMError, SearchError, AgentOutputError)):
        return True, None
    return False, None  # unexpected: fail the task, don't retry blindly


async def run_with_retries(
    ctx: RunContext,
    agent: str,
    task_key: str | None,
    max_attempts: int,
    fn: Callable[[CallLog], Awaitable[Any]],
    input_summary: dict[str, Any] | None = None,
) -> tuple[Any | None, list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Returns (result, executions, fatal_error, last_error_message)."""
    executions: list[dict[str, Any]] = []
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        ctx.check_cancelled()
        call_log, t0, started = CallLog(), time.perf_counter(), now_iso()
        try:
            result = await fn(call_log)
        except WorkflowCancelledError:
            raise
        except Exception as exc:
            retryable, fatal = classify(exc)
            last_error = f"{type(exc).__name__}: {exc}"[:1000]
            if not retryable and fatal is None:
                log.exception("agent_unexpected_error", agent=agent, task=task_key)
            executions.append(
                _execution(
                    agent,
                    call_log,
                    task_key=task_key,
                    attempt=attempt,
                    status="failed",
                    started=started,
                    t0=t0,
                    error=last_error,
                    input_=input_summary,
                )
            )
            if fatal or not retryable or attempt == max_attempts:
                return None, executions, fatal, last_error
            delay = backoff_delay(attempt - 1, base=2.0, cap=20.0)
            await ctx.emit(
                EventType.TASK_RETRY,
                agent=agent,
                task_key=task_key,
                data={"attempt": attempt, "next_in_s": round(delay, 1), "error": last_error},
            )
            await sleep(delay)
            continue
        out_summary = result.stats if isinstance(result, AgentResult) else {}
        executions.append(
            _execution(
                agent,
                call_log,
                task_key=task_key,
                attempt=attempt,
                status="succeeded",
                started=started,
                t0=t0,
                output=out_summary,
                input_=input_summary,
            )
        )
        return result, executions, None, None
    return None, executions, None, last_error  # pragma: no cover


# ------------------------------------------------------------------ planner


async def planner_node(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    ctx.check_cancelled()
    await ctx.emit(EventType.STAGE, stage="planning")
    profile = profile_for(state["depth"])

    async def call(call_log: CallLog) -> Any:
        return await run_planner(
            ctx,
            call_log,
            objective=state["objective"],
            depth=state["depth"],
            focus_areas=state.get("focus_areas", []),
            profile=profile,
        )

    attempts = 1 + state["limits"].get("max_agent_retries", 1)
    plan, executions, fatal, error = await run_with_retries(
        ctx, "planner_agent", None, attempts, call, {"objective": state["objective"][:200]}
    )
    if plan is None:
        err = fatal or {"code": "planning_failed", "message": error or "planner failed"}
        return {"executions": executions, "usage": _usage(executions), "error": err}

    tasks = {
        t.task_id: {
            "task_key": t.task_id,
            "agent": t.agent,
            "description": t.description,
            "priority": t.priority,
            "dependencies": list(t.dependencies),
            "status": "pending",
            "attempts": 0,
            "iteration": 0,
            "error": None,
            "started_at": None,
            "completed_at": None,
        }
        for t in plan.tasks
    }
    plan_dict = plan.model_dump()
    await ctx.emit(EventType.PLAN, stage="planned", data=plan_dict)
    return {
        "plan": plan_dict,
        "tasks": tasks,
        "executions": executions,
        "usage": _usage(executions),
        "stage": "planned",
    }


def route_after_planner(state: ResearchState) -> str:
    return "finalize" if state.get("error") else "approval_gate"


async def approval_gate(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Human checkpoint: pauses the graph (checkpointed) until the plan is approved.

    Kept separate from the planner so resuming never re-runs the paid planning call.
    """
    if not state["limits"].get("require_plan_approval") or state.get("plan_approved"):
        return {"plan_approved": True}
    await runtime.context.emit(
        EventType.APPROVAL_REQUIRED, stage="awaiting_approval", data={"plan": state.get("plan")}
    )
    decision = interrupt({"type": "plan_approval", "plan": state.get("plan")})
    if isinstance(decision, dict) and decision.get("approved"):
        return {"plan_approved": True, "stage": "approved"}
    reason = decision.get("reason") if isinstance(decision, dict) else None
    return {"error": {"code": "plan_rejected", "message": reason or "Plan rejected by user"}}


# ------------------------------------------------------------- scheduling


def ready_tasks(state: ResearchState) -> list[dict[str, Any]]:
    tasks = state.get("tasks", {})
    return [
        t
        for t in tasks.values()
        if t["status"] == "pending"
        and all(tasks.get(d, {}).get("status") in DONE for d in t["dependencies"])
    ]


def _payload(state: ResearchState, task: dict[str, Any]) -> TaskPayload:
    payload: TaskPayload = {
        "task": task,
        "objective": state["objective"],
        "depth": state["depth"],
        "limits": state["limits"],
        "known_urls": sorted({s["normalized_url"] for s in state.get("sources", {}).values()}),
    }
    if task["agent"] == MARKET:
        outputs = state.get("outputs", {})
        # Upstream = declared dependencies, or (follow-ups) every evidence task.
        deps = task["dependencies"] or [
            k for k, t in state.get("tasks", {}).items() if t["agent"] != MARKET and k in outputs
        ]
        upstream = {d: outputs[d] for d in deps if d in outputs}
        cited: list[str] = []
        for out in upstream.values():
            for item in out.get("findings", []) + out.get("competitors", []):
                cited += item.get("source_ids", [])
        sources = state.get("sources", {})
        payload["upstream"] = upstream
        payload["upstream_sources"] = [sources[k] for k in dict.fromkeys(cited) if k in sources]
    return payload


def route_ready(state: ResearchState) -> list[Send] | str:
    if state.get("error"):
        return "finalize"
    ready = ready_tasks(state)
    if ready:
        return [Send(t["agent"], _payload(state, t)) for t in ready]
    tasks = state.get("tasks", {}).values()
    if not any(t["status"] in DONE for t in tasks):
        return "finalize"
    return "verification_agent"


# ------------------------------------------------------------ agent nodes

Runner = Callable[
    [RunContext, CallLog, dict[str, Any], TaskPayload, DepthProfile], Awaitable[AgentResult]
]


async def _research_runner(
    ctx: RunContext,
    log_: CallLog,
    task: dict[str, Any],
    payload: TaskPayload,
    profile: DepthProfile,
) -> AgentResult:
    return await run_research(
        ctx,
        log_,
        task=task,
        objective=payload["objective"],
        profile=profile,
        known_urls=set(payload["known_urls"]),
    )


async def _competitor_runner(
    ctx: RunContext,
    log_: CallLog,
    task: dict[str, Any],
    payload: TaskPayload,
    profile: DepthProfile,
) -> AgentResult:
    return await run_competitor(
        ctx,
        log_,
        task=task,
        objective=payload["objective"],
        profile=profile,
        known_urls=set(payload["known_urls"]),
    )


async def _market_runner(
    ctx: RunContext,
    log_: CallLog,
    task: dict[str, Any],
    payload: TaskPayload,
    profile: DepthProfile,
) -> AgentResult:
    sources = [SourceRecord.model_validate(s) for s in payload.get("upstream_sources", [])]
    return await run_market_analysis(
        ctx,
        log_,
        task=task,
        objective=payload["objective"],
        profile=profile,
        upstream=payload.get("upstream", {}),
        sources=sources,
    )


def make_agent_node(agent: str, runner: Runner) -> Any:
    """Typed ``Any``: LangGraph's node protocols match on parameter names, which a
    ``Callable[...]`` annotation cannot express."""

    async def node(payload: TaskPayload, runtime: Runtime[RunContext]) -> dict[str, Any]:
        ctx = runtime.context
        ctx.check_cancelled()
        task = dict(payload["task"])
        key = task["task_key"]
        profile = profile_for(payload["depth"])
        task.update(status="running", started_at=now_iso())
        await ctx.emit(
            EventType.TASK_STARTED,
            stage="researching",
            agent=agent,
            task_key=key,
            data={"description": task["description"]},
        )

        max_attempts = 1 + payload["limits"].get("max_agent_retries", 1)
        result, executions, fatal, error = await run_with_retries(
            ctx,
            agent,
            key,
            max_attempts,
            lambda call_log: runner(ctx, call_log, task, payload, profile),
            {"description": task["description"][:300]},
        )
        task["attempts"] = task.get("attempts", 0) + len(executions)
        task["completed_at"] = now_iso()
        update: dict[str, Any] = {"executions": executions, "usage": _usage(executions)}
        if result is None:
            task.update(status="failed", error=error)
            await ctx.emit(
                EventType.TASK_FAILED,
                agent=agent,
                task_key=key,
                data={"error": error, "fatal": bool(fatal)},
            )
            update["tasks"] = {key: task}
            if fatal:
                update["error"] = fatal
            return update

        task.update(status="completed", error=None)
        await ctx.emit(EventType.TASK_COMPLETED, agent=agent, task_key=key, data=result.stats)
        update.update(
            tasks={key: task},
            outputs={key: result.output},
            task_stats={key: result.stats},
            sources={s.citation_key: s.model_dump(mode="json") for s in result.sources},
        )
        return update

    node.__name__ = f"{agent}_node"
    return node


research_node = make_agent_node(RESEARCH, _research_runner)
competitor_node = make_agent_node(COMPETITOR, _competitor_runner)
market_node = make_agent_node(MARKET, _market_runner)


# ---------------------------------------------------------- evidence join


async def collect_evidence(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Join point after each parallel superstep: skip blocked tasks, extract claims."""
    ctx = runtime.context
    ctx.check_cancelled()
    tasks = {k: dict(v) for k, v in state.get("tasks", {}).items()}
    updates: dict[str, dict[str, Any]] = {}
    changed = True
    while changed:  # propagate failures down the DAG
        changed = False
        for key, t in tasks.items():
            if t["status"] == "pending" and any(
                tasks.get(d, {}).get("status") in DEAD for d in t["dependencies"]
            ):
                t.update(status="skipped", error="dependency did not complete")
                updates[key] = t
                changed = True
    for key in updates:
        await ctx.emit(
            EventType.TASK_SKIPPED,
            task_key=key,
            agent=tasks[key]["agent"],
            data={"reason": "dependency did not complete"},
        )

    claimed = {c["origin_task"] for c in state.get("claims", {}).values()}
    new_claims: dict[str, dict[str, Any]] = {}
    for key, out in state.get("outputs", {}).items():
        if key in claimed or tasks.get(key, {}).get("status") != "completed":
            continue
        for c in build_claims(key, tasks[key]["agent"], out, tasks[key]["priority"]):
            new_claims[c.claim_id] = c.model_dump()

    await ctx.emit(
        EventType.STAGE,
        stage="collecting",
        data={
            "sources": len(state.get("sources", {})),
            "new_claims": len(new_claims),
            "completed_tasks": sum(t["status"] == "completed" for t in tasks.values()),
        },
    )
    return {"tasks": updates, "claims": new_claims, "stage": "collecting"}


# ------------------------------------------------------------ verification


async def verification_node(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    ctx.check_cancelled()
    await ctx.emit(EventType.STAGE, stage="verifying")
    sources = load_sources(state)
    claims = load_claims(state)
    done = state.get("verdicts", {})
    pending = [c for c in claims if c.claim_id not in done]

    async def call(call_log: CallLog) -> Any:
        return await verify_claims(ctx, call_log, claims=pending, sources=sources)

    executions: list[dict[str, Any]] = []
    new_verdicts: dict[str, dict[str, Any]] = {}
    queries: list[str] = []
    if pending:
        attempts = 1 + state["limits"].get("max_agent_retries", 1)
        result, executions, fatal, error = await run_with_retries(
            ctx, "verification_agent", None, attempts, call, {"claims": len(pending)}
        )
        if result is None:
            err = fatal or {
                "code": "verification_failed",
                "message": error or "verification failed",
            }
            return {"executions": executions, "usage": _usage(executions), "error": err}
        new_verdicts, queries = result

    all_verdicts = {**done, **new_verdicts}
    gaps = [
        g
        for o in state.get("outputs", {}).values()
        for g in o.get("gaps", []) + o.get("unknowns", [])
    ]
    summary = score_verification(
        claims,
        all_verdicts,
        sources,
        profile_for(state["depth"]),
        ctx.settings.source_max_age_years,
        queries,
        gaps,
        latest_round={c.claim_id for c in pending},
    )
    await ctx.emit(
        EventType.VERIFICATION,
        stage="verified",
        data={
            k: summary[k] for k in ("score", "reliability_score", "sufficient", "reasons", "counts")
        },
    )
    return {
        "verdicts": new_verdicts,
        "verification": summary,
        "executions": executions,
        "usage": _usage(executions),
        "stage": "verified",
    }


def route_after_verification(state: ResearchState) -> str:
    if state.get("error"):
        return "finalize"
    v = state.get("verification") or {}
    if v.get("sufficient"):
        return "report_agent"
    can_iterate = state.get("iteration", 0) < state["limits"].get("max_research_iterations", 0)
    if can_iterate and (v.get("recommended_queries") or v.get("gaps")):
        return "plan_followup"
    usable = v.get("counts", {}).get("supported", 0) + v.get("counts", {}).get(
        "partially_supported", 0
    )
    return "report_agent" if usable else "finalize"


async def plan_followup(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Return to research: new tasks targeting the verifier's gaps (bounded by iterations)."""
    ctx = runtime.context
    ctx.check_cancelled()
    iteration = state.get("iteration", 0) + 1
    v = state.get("verification") or {}
    queries = list(v.get("recommended_queries", []))
    gaps = list(v.get("gaps", []))
    reasons = "; ".join(v.get("reasons", []))
    new: dict[str, dict[str, Any]] = {}
    chunks = [queries[i : i + 3] for i in range(0, len(queries), 3)] or [[]]
    for n, chunk in enumerate(chunks[:MAX_FOLLOWUP_TASKS], 1):
        key = f"task_{iteration}{n:02d}"
        focus = "; ".join(gaps[(n - 1) * 3 : n * 3]) or reasons
        new[key] = {
            "task_key": key,
            "agent": RESEARCH,
            "priority": "high",
            "dependencies": [],
            "description": f"Follow-up research (round {iteration}) to fill evidence gaps: {focus}"[
                :500
            ],
            "seed_queries": chunk,
            "status": "pending",
            "attempts": 0,
            "iteration": iteration,
            "error": None,
            "started_at": None,
            "completed_at": None,
        }
    await ctx.emit(
        EventType.STAGE,
        stage="follow_up_research",
        data={"iteration": iteration, "tasks": list(new), "reasons": v.get("reasons", [])},
    )
    return {"tasks": new, "iteration": iteration, "stage": "follow_up_research"}


# ------------------------------------------------------------------ report


async def report_node(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    ctx.check_cancelled()
    await ctx.emit(EventType.STAGE, stage="reporting")
    sources = load_sources(state)
    claims = load_claims(state)
    verdicts = state.get("verdicts", {})
    verification = state.get("verification") or {}
    outputs = state.get("outputs", {})
    tasks = state.get("tasks", {})
    digest = build_digest(claims, verdicts, outputs, verification)

    async def call(call_log: CallLog) -> Any:
        return await write_report(ctx, call_log, objective=state["objective"], digest=digest)

    attempts = 1 + state["limits"].get("max_agent_retries", 1)
    result, executions, fatal, error = await run_with_retries(
        ctx, "report_agent", None, attempts, call, {"digest_chars": len(digest)}
    )
    report_attempts = state.get("report_attempts", 0) + 1
    if result is None:
        err = fatal or {"code": "report_failed", "message": error or "report generation failed"}
        return {
            "executions": executions,
            "usage": _usage(executions),
            "error": err,
            "report_attempts": report_attempts,
        }

    core, strategy = result
    core, strategy, validation = validate_report(core, strategy, set(sources))
    cited = [k for k in cited_keys(core, strategy) if k in sources]
    stats = {
        "queries": sum(len(s.get("queries", [])) for s in state.get("task_stats", {}).values()),
        "fetched": sum(1 for s in sources.values() if s.fetched),
    }
    markdown = render_markdown(
        title=state.get("title") or "Research report",
        objective=state["objective"],
        core=core,
        strategy=strategy,
        sources=sources,
        cited=cited,
        tasks=tasks,
        outputs=outputs,
        verification=verification,
        stats=stats,
        extra_limitations=evidence_limitations(verification, tasks, sources, cited),
    )
    report = {
        "markdown": markdown,
        "core": core.model_dump(),
        "strategy": strategy.model_dump(),
        "cited_sources": cited,
        "validation": validation,
        "verification_score": verification.get("score"),
        "evidence_sufficient": verification.get("sufficient"),
    }
    await ctx.emit(
        EventType.REPORT, stage="reported", data={"cited_sources": len(cited), **validation}
    )
    return {
        "report": report,
        "report_attempts": report_attempts,
        "executions": executions,
        "usage": _usage(executions),
        "stage": "reported",
    }


def route_after_report(state: ResearchState) -> str:
    return "finalize" if state.get("error") else "quality_check"


async def quality_check_node(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    report = state.get("report") or {}
    sources = load_sources(state)
    q = quality_check(
        # model_construct: content was validated by the report node and then
        # deliberately pruned (e.g. uncited findings dropped), which may leave
        # lists the input schema would reject. Judging that is this node's job.
        ReportCore.model_construct(**report["core"]),
        ReportStrategy.model_construct(**report["strategy"]),
        report.get("cited_sources", []),
        state.get("verification") or {},
        report.get("validation", {}),
        state.get("tasks", {}),
        sources,
    )
    await runtime.context.emit(EventType.STAGE, stage="quality_checked", data=q)
    return {"quality": q, "stage": "quality_checked"}


def route_after_quality(state: ResearchState) -> str:
    q = state.get("quality") or {}
    if q.get("passed"):
        return "complete"
    if state.get("report_attempts", 0) < MAX_REPORT_ATTEMPTS:
        return "report_agent"
    return "finalize"


# ------------------------------------------------------------------ endings


async def complete_node(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    await runtime.context.emit(
        EventType.COMPLETED,
        stage="completed",
        data={
            "usage": state.get("usage", {}),
            "warnings": (state.get("quality") or {}).get("warnings", []),
        },
    )
    return {"status": "completed", "stage": "completed"}


async def finalize_node(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    error = state.get("error")
    if error is None:
        q = state.get("quality")
        v = state.get("verification")
        if q and not q.get("passed"):
            error = {"code": "quality_check_failed", "message": "; ".join(q["hard_failures"])}
        elif v is not None:
            error = {
                "code": "insufficient_evidence",
                "message": "No claims could be verified: " + "; ".join(v.get("reasons", [])),
            }
        else:
            error = {"code": "all_tasks_failed", "message": "No research task completed"}
    status = "cancelled" if error["code"] == "plan_rejected" else "failed"
    await runtime.context.emit(EventType.ERROR, stage=status, data=error)
    return {"status": status, "stage": status, "error": error}
