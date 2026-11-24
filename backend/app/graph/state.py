"""Typed workflow state.

Every key written by parallel branches has a reducer, so concurrent updates
merge deterministically instead of raising. Values are JSON-friendly (dicts,
lists, strings, numbers) so checkpoints serialise cleanly to Postgres.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.schemas.agents import Claim
from app.schemas.evidence import SourceRecord


def merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


def add_usage(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    a, b = a or {}, b or {}
    return {
        "prompt_tokens": a.get("prompt_tokens", 0) + b.get("prompt_tokens", 0),
        "completion_tokens": a.get("completion_tokens", 0) + b.get("completion_tokens", 0),
        "cost_usd": round(a.get("cost_usd", 0.0) + b.get("cost_usd", 0.0), 6),
        "llm_calls": a.get("llm_calls", 0) + b.get("llm_calls", 0),
    }


def first_error(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    return a or b


def last_value(a: Any, b: Any) -> Any:
    return b if b is not None else a


class ResearchState(TypedDict, total=False):
    # inputs
    project_id: str
    title: str
    objective: str
    depth: str
    focus_areas: list[str]
    limits: dict[str, Any]
    # planning
    plan: dict[str, Any] | None
    plan_approved: bool
    # execution
    tasks: Annotated[dict[str, dict[str, Any]], merge_dicts]
    sources: Annotated[dict[str, dict[str, Any]], merge_dicts]
    outputs: Annotated[dict[str, dict[str, Any]], merge_dicts]
    task_stats: Annotated[dict[str, dict[str, Any]], merge_dicts]
    # verification
    claims: Annotated[dict[str, dict[str, Any]], merge_dicts]
    verdicts: Annotated[dict[str, dict[str, Any]], merge_dicts]
    verification: dict[str, Any] | None
    iteration: int
    # reporting
    report: dict[str, Any] | None
    report_attempts: int
    quality: dict[str, Any] | None
    # bookkeeping
    executions: Annotated[list[dict[str, Any]], operator.add]
    usage: Annotated[dict[str, Any], add_usage]
    stage: Annotated[str | None, last_value]
    error: Annotated[dict[str, Any] | None, first_error]
    status: str


class TaskPayload(TypedDict, total=False):
    """Input of a specialist agent node, delivered by ``Send`` (one per ready task)."""

    task: dict[str, Any]
    objective: str
    depth: str
    limits: dict[str, Any]
    known_urls: list[str]
    upstream: dict[str, dict[str, Any]]
    upstream_sources: list[dict[str, Any]]


def initial_state(
    *,
    project_id: str,
    title: str,
    objective: str,
    depth: str,
    focus_areas: list[str],
    limits: dict[str, Any],
) -> ResearchState:
    return ResearchState(
        project_id=project_id,
        title=title,
        objective=objective,
        depth=depth,
        focus_areas=focus_areas,
        limits=limits,
        plan=None,
        plan_approved=False,
        tasks={},
        sources={},
        outputs={},
        task_stats={},
        claims={},
        verdicts={},
        verification=None,
        iteration=0,
        report=None,
        report_attempts=0,
        quality=None,
        executions=[],
        usage={},
        stage="queued",
        error=None,
        status="running",
    )


def load_sources(state: ResearchState) -> dict[str, SourceRecord]:
    return {k: SourceRecord.model_validate(v) for k, v in (state.get("sources") or {}).items()}


def load_claims(state: ResearchState) -> list[Claim]:
    return [Claim.model_validate(c) for c in (state.get("claims") or {}).values()]
