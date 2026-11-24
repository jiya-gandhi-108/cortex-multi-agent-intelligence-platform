"""Market Analysis Agent: synthesises evidence from upstream tasks (no web access).

Every insight is labelled verified_fact / estimate / interpretation /
recommendation. A ``verified_fact`` with no valid citation cannot be a
verified fact, so it is downgraded to ``interpretation`` (counted in stats);
estimates are never relabelled as facts.
"""

from __future__ import annotations

import json
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
from app.agents.research import AgentResult, _valid
from app.llm.base import ModelTier
from app.schemas.agents import MarketOutput
from app.schemas.evidence import SourceRecord

ROLE = """You are the market analysis agent. You receive structured findings from other
agents plus excerpts of their sources. Identify market trends, customer segments,
opportunities, risks and patterns that answer the objective.
Label every insight precisely:
- verified_fact: directly stated by at least one cited source
- estimate: a number or projection a source presents as an estimate/forecast
- interpretation: your reasoning across several pieces of evidence
- recommendation: an action you suggest based on the evidence
Never present an estimate or interpretation as a verified_fact. Cite source ids for every
verified_fact and estimate; interpretations and recommendations should cite the evidence
they build on. State the reasoning briefly in rationale."""


def _compact_upstream(upstream: dict[str, dict[str, Any]]) -> str:
    """Findings/competitor data as compact JSON (no source text; that comes separately)."""
    compact: dict[str, Any] = {}
    for task_key, out in upstream.items():
        if "findings" in out:
            compact[task_key] = {
                "summary": out.get("summary", "")[:600],
                "findings": [
                    {"claim": f["claim"], "type": f["evidence_type"], "sources": f["source_ids"]}
                    for f in out["findings"]
                ],
                "gaps": out.get("gaps", []),
            }
        elif "competitors" in out:
            compact[task_key] = {
                "summary": out.get("summary", "")[:600],
                "competitors": [
                    {
                        k: c[k]
                        for k in (
                            "name",
                            "products",
                            "pricing",
                            "target_customers",
                            "positioning",
                            "differentiators",
                            "source_ids",
                        )
                    }
                    for c in out["competitors"]
                ],
                "market_gaps": out.get("market_gaps", []),
                "unknowns": out.get("unknowns", []),
            }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


async def run_market_analysis(
    ctx: RunContext,
    log: CallLog,
    *,
    task: dict[str, Any],
    objective: str,
    profile: DepthProfile,
    upstream: dict[str, dict[str, Any]],
    sources: list[SourceRecord],
) -> AgentResult:
    evidence_json = _compact_upstream(upstream)
    head = (
        f"<objective>\n{objective}\n</objective>\n\nTask: {task['description']}\n\n"
        f"<upstream_findings>\n{evidence_json}\n</upstream_findings>\n\n<sources>\n"
    )
    tail = "\n</sources>\n\nCite only these source ids: " + ", ".join(
        s.citation_key for s in sources
    )
    if len(head) > prompt_char_budget(ctx.settings, 3_000):
        # Keep upstream JSON within budget: findings matter more than excerpts.
        head = head[: prompt_char_budget(ctx.settings, 3_000)] + "\n…(truncated)\n<sources>\n"
    budget = prompt_char_budget(ctx.settings, len(head) + len(tail) + 2_500)
    excerpts = (
        render_sources(sources, min(600, profile.excerpt_chars), budget) if budget > 0 else ""
    )

    result = await structured_call(
        ctx,
        log,
        agent=task["agent"],
        output_model=MarketOutput,
        system=system_prompt(ROLE),
        user=head + excerpts + tail,
        tier=ModelTier.REASONING,
        max_completion_tokens=2_500,
        task_key=task["task_key"],
    )
    allowed = {s.citation_key for s in sources}
    rejected = downgraded = 0
    for ins in result.insights:
        ins.source_ids, bad = _valid(ins.source_ids, allowed)
        rejected += bad
        if ins.kind == "verified_fact" and not ins.source_ids:
            ins.kind = "interpretation"
            downgraded += 1
    return AgentResult(
        result.model_dump(),
        [],
        {
            "insights": len(result.insights),
            "rejected_citations": rejected,
            "downgraded_facts": downgraded,
        },
    )
