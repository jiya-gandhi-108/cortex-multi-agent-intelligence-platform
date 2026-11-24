"""Research Agent and Competitor Intelligence Agent.

Both follow the same evidence discipline:
  1. the LLM writes search queries for the task (it never supplies URLs),
  2. the Evidence Collector retrieves real sources,
  3. the LLM extracts structured output citing source ids,
  4. citations are checked against the retrieved set; unknown ids are stripped,
     and items left without evidence are dropped (counted in stats).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.base import (
    CallLog,
    RunContext,
    prompt_char_budget,
    render_sources,
    structured_call,
    system_prompt,
)
from app.agents.profiles import DepthProfile
from app.graph.events import EventType
from app.llm.base import ModelTier
from app.schemas.agents import CompetitorOutput, ResearchOutput, SearchQueries
from app.schemas.evidence import SourceRecord

QUERY_ROLE = """You write web search queries for a research task.
Write short, specific keyword queries (3-12 words) a search engine will answer well.
Cover different angles of the task; do not repeat near-identical queries.
Include the geography/industry from the objective when relevant. No URLs, no quotes
around the whole query, no site: operators."""

RESEARCH_ROLE = """You are the research agent. Extract the facts the task needs from the
provided sources. Each finding must be a single, specific, checkable claim supported by
the cited sources. Classify each finding:
- fact: directly stated by a source
- statistic: a number stated by a source (keep the source's figure and unit, and its year if given)
- estimate: a projection or estimate made by a source (say whose estimate)
- opinion: a view or prediction attributed to someone
List important information you could not find under gaps."""

COMPETITOR_ROLE = """You are the competitor intelligence agent. From the provided sources,
identify competitors relevant to the task and describe their products, pricing, target
customers, positioning and differentiators.
- pricing: only if a cited source states it; otherwise null. Put the sources that state
  the price in pricing_source_ids. Never estimate or infer prices.
- Any field the sources do not cover must be null or an empty list; list notable unknowns
  under unknowns.
- market_gaps: underserved needs that the sources support."""


@dataclass
class AgentResult:
    output: dict[str, Any]
    sources: list[SourceRecord]
    stats: dict[str, Any] = field(default_factory=dict)


def _query_validator(q: SearchQueries) -> list[str]:
    errs = []
    for query in q.queries:
        if not 3 <= len(query.strip()) <= 200:
            errs.append(f"query length must be 3-200 characters: {query[:50]!r}")
        if "http://" in query or "https://" in query:
            errs.append("queries must not contain URLs")
    return errs


async def _queries(
    ctx: RunContext, log: CallLog, task: dict[str, Any], objective: str, profile: DepthProfile
) -> list[str]:
    seeds = [q.strip() for q in task.get("seed_queries") or [] if 3 <= len(q.strip()) <= 200]
    if seeds:  # follow-up tasks carry queries the verifier recommended for the gaps
        return list(dict.fromkeys(seeds))[: profile.queries_per_task]
    user = (
        f"<objective>\n{objective}\n</objective>\n\n"
        f"Task ({task['agent']}): {task['description']}\n\n"
        f"Write up to {profile.queries_per_task} search queries."
    )
    out = await structured_call(
        ctx,
        log,
        agent=task["agent"],
        output_model=SearchQueries,
        system=system_prompt(QUERY_ROLE, untrusted=False),
        user=user,
        tier=ModelTier.FAST,
        max_completion_tokens=500,
        validate=_query_validator,
        task_key=task["task_key"],
    )
    seen: set[str] = set()
    unique = []
    for q in out.queries:
        key = q.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(q.strip())
    return unique[: profile.queries_per_task]


async def _collect(
    ctx: RunContext,
    task: dict[str, Any],
    queries: list[str],
    profile: DepthProfile,
    known_urls: set[str],
) -> tuple[list[SourceRecord], dict[str, Any]]:
    async def on_tool(name: str, data: dict[str, Any]) -> None:
        await ctx.emit(
            EventType.TOOL_CALL,
            agent=task["agent"],
            task_key=task["task_key"],
            data={"tool": name, **data},
        )

    pending: list[tuple[str, dict[str, Any]]] = []
    try:
        result = await ctx.collector.collect(
            task["task_key"],
            queries,
            known_urls,
            limit=profile.sources_per_task,
            on_tool_call=lambda name, data: pending.append((name, data)),
        )
    finally:
        for name, data in pending:
            await on_tool(name, data)
    stats = {
        "queries": queries,
        "sources_retrieved": len(result.sources),
        "pages_fetched": sum(s.fetched for s in result.sources),
        "fetch_failures": result.failures,
        "search_errors": result.search_errors,
    }
    await ctx.emit(
        EventType.SOURCES,
        agent=task["agent"],
        task_key=task["task_key"],
        data={
            "count": len(result.sources),
            "citation_keys": [s.citation_key for s in result.sources],
        },
    )
    return result.sources, stats


def _valid(ids: list[str], allowed: set[str]) -> tuple[list[str], int]:
    kept = [i for i in dict.fromkeys(ids) if i in allowed]
    return kept, len(set(ids)) - len(kept)


def _user_prompt(
    ctx: RunContext,
    objective: str,
    task: dict[str, Any],
    sources: list[SourceRecord],
    profile: DepthProfile,
) -> str:
    head = f"<objective>\n{objective}\n</objective>\n\nTask: {task['description']}\n\n<sources>\n"
    tail = "\n</sources>\n\nCite only these source ids: " + ", ".join(
        s.citation_key for s in sources
    )
    budget = prompt_char_budget(ctx.settings, len(head) + len(tail) + 2_500)
    return head + render_sources(sources, profile.excerpt_chars, budget) + tail


async def run_research(
    ctx: RunContext,
    log: CallLog,
    *,
    task: dict[str, Any],
    objective: str,
    profile: DepthProfile,
    known_urls: set[str],
) -> AgentResult:
    queries = await _queries(ctx, log, task, objective, profile)
    sources, stats = await _collect(ctx, task, queries, profile, known_urls)
    if not sources:
        out = ResearchOutput(
            summary="No sources could be retrieved for this task.",
            findings=[],
            gaps=[task["description"]],
        )
        return AgentResult(out.model_dump(), [], {**stats, "no_evidence": True})

    result = await structured_call(
        ctx,
        log,
        agent=task["agent"],
        output_model=ResearchOutput,
        system=system_prompt(RESEARCH_ROLE),
        user=_user_prompt(ctx, objective, task, sources, profile),
        tier=ModelTier.FAST,
        max_completion_tokens=2_000,
        task_key=task["task_key"],
    )
    allowed = {s.citation_key for s in sources}
    rejected, kept = 0, []
    for f in result.findings:
        f.source_ids, bad = _valid(f.source_ids, allowed)
        rejected += bad
        if f.source_ids:
            kept.append(f)
    dropped = len(result.findings) - len(kept)
    result.findings = kept
    return AgentResult(
        result.model_dump(),
        sources,
        {
            **stats,
            "findings": len(kept),
            "rejected_citations": rejected,
            "dropped_findings": dropped,
        },
    )


async def run_competitor(
    ctx: RunContext,
    log: CallLog,
    *,
    task: dict[str, Any],
    objective: str,
    profile: DepthProfile,
    known_urls: set[str],
) -> AgentResult:
    queries = await _queries(ctx, log, task, objective, profile)
    sources, stats = await _collect(ctx, task, queries, profile, known_urls)
    if not sources:
        out = CompetitorOutput(
            summary="No sources could be retrieved for this task.",
            competitors=[],
            market_gaps=[],
            unknowns=[task["description"]],
        )
        return AgentResult(out.model_dump(), [], {**stats, "no_evidence": True})

    result = await structured_call(
        ctx,
        log,
        agent=task["agent"],
        output_model=CompetitorOutput,
        system=system_prompt(COMPETITOR_ROLE),
        user=_user_prompt(ctx, objective, task, sources, profile),
        tier=ModelTier.REASONING,
        max_completion_tokens=2_500,
        task_key=task["task_key"],
    )
    allowed = {s.citation_key for s in sources}
    rejected = pricing_redacted = 0
    competitors = []
    for c in result.competitors:
        c.source_ids, bad = _valid(c.source_ids, allowed)
        c.pricing_source_ids, bad_p = _valid(c.pricing_source_ids, allowed)
        rejected += bad + bad_p
        if c.pricing and not c.pricing_source_ids:
            c.pricing = None  # unsourced price -> unknown, never shown as fact
            pricing_redacted += 1
        if c.source_ids:
            competitors.append(c)
    dropped = len(result.competitors) - len(competitors)
    result.competitors = competitors
    gaps = []
    for g in result.market_gaps:
        g.source_ids, bad = _valid(g.source_ids, allowed)
        rejected += bad
        gaps.append(g)
    result.market_gaps = gaps
    return AgentResult(
        result.model_dump(),
        sources,
        {
            **stats,
            "competitors": len(competitors),
            "rejected_citations": rejected,
            "dropped_competitors": dropped,
            "pricing_redacted": pricing_redacted,
        },
    )
