"""Planning Agent: objective -> validated task DAG.

Shape is enforced by the strict schema (agent names are an enum, so the
model cannot invent one). Semantics are enforced here; violations go back to
the model once as repair feedback, then fail the run.
"""

from __future__ import annotations

from collections import deque

from app.agents.base import CallLog, RunContext, structured_call, system_prompt
from app.agents.profiles import DepthProfile
from app.llm.base import ModelTier
from app.schemas.agents import PlanOutput

AGENT = "planner_agent"

ROLE = """You are the planning agent of a multi-agent research system.
Decompose the user's research objective into a small set of concrete subtasks and assign
each to exactly one specialist:

- research_agent: searches the web for facts, statistics, trends, regulation, technology.
- competitor_agent: identifies companies/products and gathers their offerings, pricing,
  target customers and positioning from the web.
- market_analysis_agent: performs NO searching. It analyses evidence produced by other
  tasks to identify trends, segments, opportunities and risks. It must therefore depend
  on at least one research_agent or competitor_agent task.

Rules:
- task_id format is task_001, task_002, ... in order.
- dependencies list task_ids that must finish first; use [] when independent so tasks
  can run in parallel. No cycles, no self-dependencies.
- Each description must be specific enough to drive web searches on its own.
- Use priority "high" for tasks the objective cannot be answered without."""


def validate_plan(plan: PlanOutput, max_tasks: int) -> list[str]:
    errors: list[str] = []
    ids = [t.task_id for t in plan.tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"duplicate task_ids: {dupes}")
    if len(plan.tasks) > max_tasks:
        errors.append(f"too many tasks: {len(plan.tasks)} (max {max_tasks} for this depth)")
    known = set(ids)
    by_id = {t.task_id: t for t in plan.tasks}
    for t in plan.tasks:
        if t.task_id in t.dependencies:
            errors.append(f"{t.task_id} depends on itself")
        missing = sorted(set(t.dependencies) - known)
        if missing:
            errors.append(f"{t.task_id} depends on unknown tasks {missing}")
        if t.agent == "market_analysis_agent":
            feeders = [
                d
                for d in t.dependencies
                if d in by_id and by_id[d].agent in ("research_agent", "competitor_agent")
            ]
            if not feeders:
                errors.append(
                    f"{t.task_id} (market_analysis_agent) must depend on at least one "
                    "research_agent or competitor_agent task"
                )
    if not any(t.agent in ("research_agent", "competitor_agent") for t in plan.tasks):
        errors.append("plan needs at least one research_agent or competitor_agent task")
    if not errors and has_cycle({t.task_id: list(t.dependencies) for t in plan.tasks}):
        errors.append("task dependencies contain a cycle")
    return errors


def has_cycle(graph: dict[str, list[str]]) -> bool:
    indegree = {n: 0 for n in graph}
    for node, deps in graph.items():
        for _ in deps:
            indegree[node] += 1
    dependents: dict[str, list[str]] = {n: [] for n in graph}
    for node, deps in graph.items():
        for d in deps:
            dependents.setdefault(d, []).append(node)
    queue = deque(n for n, deg in indegree.items() if deg == 0)
    seen = 0
    while queue:
        n = queue.popleft()
        seen += 1
        for m in dependents.get(n, []):
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)
    return seen != len(graph)


async def run_planner(
    ctx: RunContext,
    log: CallLog,
    *,
    objective: str,
    depth: str,
    focus_areas: list[str],
    profile: DepthProfile,
) -> PlanOutput:
    focus = (
        ", ".join(focus_areas) if focus_areas else "none specified (cover what the objective needs)"
    )
    user = (
        f"<objective>\n{objective}\n</objective>\n\n"
        f"Research depth: {depth} (at most {profile.max_tasks} tasks).\n"
        f"Focus areas requested by the user: {focus}.\n"
        "Produce the plan."
    )
    return await structured_call(
        ctx,
        log,
        agent=AGENT,
        output_model=PlanOutput,
        system=system_prompt(ROLE, untrusted=False),
        user=user,
        tier=ModelTier.REASONING,
        max_completion_tokens=2_000,
        validate=lambda p: validate_plan(p, profile.max_tasks),
    )
