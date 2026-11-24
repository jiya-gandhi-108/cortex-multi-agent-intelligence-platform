"""Evaluation metrics computed from a finished workflow state.

All metrics are derived from what the run actually produced; nothing is
estimated or defaulted to a favourable value. Where a metric is an automated
proxy for a qualitative judgement (source relevance, evidence coverage), its
definition says so and human review supplements it.
"""

from __future__ import annotations

import re
from statistics import mean, median
from typing import Any

from app.evaluation.benchmarks import Benchmark, Topic

CITE_RE = re.compile(r"\[(S[0-9a-f]{6})\]")

DEFINITIONS: dict[str, str] = {
    "run_completed": "1 if the workflow ended in status 'completed', else 0.",
    "task_completion_rate": "Completed research tasks / planned research tasks (follow-ups included).",
    "source_relevance": "Automated lexical proxy: share of retrieved sources whose title or text mentions at "
    "least one keyword of the benchmark's topics. Human review rates relevance properly.",
    "evidence_coverage": "Share of the benchmark's required topics covered by at least one claim that "
    "verification judged supported or partially supported (keyword match on the claim text).",
    "citation_correctness": "Valid citations / all citations attempted. Attempted = citations kept in the "
    "report + invalid citations removed from the report + citations to unretrieved sources that agents "
    "made and the pipeline stripped. A citation is valid only if it points at a source actually retrieved.",
    "unsupported_claim_rate": "(Unsupported + contradicted claims) / all claims checked by verification.",
    "agent_failure_rate": "Failed agent executions (attempts) / all agent executions.",
    "latency_s": "Wall-clock duration of the workflow.",
    "total_tokens": "Prompt + completion tokens across all LLM calls.",
    "cost_usd": "Estimated from configured provider pricing; meaningful only when pricing_known is true.",
}


def _matches(text: str, topic: Topic) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in topic.keywords)


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def compute_metrics(
    state: dict[str, Any], benchmark: Benchmark, duration_s: float
) -> dict[str, Any]:
    tasks = list((state.get("tasks") or {}).values())
    sources = state.get("sources") or {}
    claims = list((state.get("claims") or {}).values())
    verdicts = state.get("verdicts") or {}
    report = state.get("report") or {}
    usage = state.get("usage") or {}
    executions = state.get("executions") or []

    # source relevance (lexical proxy)
    relevant = sum(
        1
        for s in sources.values()
        if any(
            _matches(f"{s.get('title') or ''} {(s.get('content') or '')[:4000]}", t)
            for t in benchmark.topics
        )
    )

    # evidence coverage: topics backed by usable (supported/partial) claims
    usable_text = [
        c["text"]
        for c in claims
        if (verdicts.get(c["claim_id"]) or {}).get("verdict")
        in ("supported", "partially_supported")
    ]
    covered = [t.name for t in benchmark.topics if any(_matches(text, t) for text in usable_text)]

    # citations
    kept = 0
    out_of_set = 0
    core, strategy = report.get("core") or {}, report.get("strategy") or {}
    texts = [
        core.get("executive_summary", ""),
        core.get("market_analysis", ""),
        core.get("competitor_analysis", ""),
    ]
    lists: list[list[str]] = []
    for f in core.get("key_findings", []):
        texts.append(f.get("text", ""))
        lists.append(f.get("citations", []))
    for group in ("opportunities", "risks", "recommendations"):
        for p in strategy.get(group, []):
            texts.append(p.get("description", ""))
            lists.append(p.get("citations", []))
    cited = [k for t in texts for k in CITE_RE.findall(t)] + [k for lst in lists for k in lst]
    for k in cited:
        if k in sources:
            kept += 1
        else:
            out_of_set += 1  # should be impossible after validation; counted, never hidden
    removed = (report.get("validation") or {}).get("invalid_citations_removed", 0)
    stripped = sum(
        (s or {}).get("rejected_citations", 0) for s in (state.get("task_stats") or {}).values()
    )
    attempted = kept + out_of_set + removed + stripped

    counts = (state.get("verification") or {}).get("counts", {})
    total_claims = counts.get("total", len(claims))
    bad_claims = counts.get("unsupported", 0) + counts.get("contradicted", 0)
    failed_execs = sum(1 for e in executions if e.get("status") == "failed")

    return {
        "status": state.get("status"),
        "error_code": (state.get("error") or {}).get("code"),
        "run_completed": 1 if state.get("status") == "completed" else 0,
        "task_completion_rate": _ratio(sum(t["status"] == "completed" for t in tasks), len(tasks)),
        "source_relevance": _ratio(relevant, len(sources)),
        "evidence_coverage": _ratio(len(covered), len(benchmark.topics)),
        "topics_covered": covered,
        "topics_missing": [t.name for t in benchmark.topics if t.name not in covered],
        "citation_correctness": _ratio(kept, attempted),
        "citations": {
            "kept": kept,
            "out_of_set": out_of_set,
            "removed_in_report": removed,
            "stripped_at_agents": stripped,
        },
        "unsupported_claim_rate": _ratio(bad_claims, total_claims),
        "claims": {"total": total_claims, "unsupported_or_contradicted": bad_claims},
        "agent_failure_rate": _ratio(failed_execs, len(executions)),
        "executions": {"total": len(executions), "failed": failed_execs},
        "latency_s": round(duration_s, 2),
        "total_tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
        "cost_usd": round(float(usage.get("cost_usd", 0.0)), 6),
        "tasks": len(tasks),
        "sources": len(sources),
        "evidence_score": (state.get("verification") or {}).get("evidence_score"),
        "follow_up_rounds": state.get("iteration", 0),
    }


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(float(mean(present)), 4) if present else None


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return round(ordered[idx], 2)


def aggregate(results: list[dict[str, Any]], pricing_known: bool) -> dict[str, Any]:
    """Suite-level summary. Content metrics average over runs that produced output
    (n is reported for each, so averages over few runs aren't over-read)."""
    metrics = [r["metrics"] for r in results if r.get("metrics")]
    latencies = [m["latency_s"] for m in metrics]
    tasks_total = sum(m.get("tasks", 0) for m in metrics)
    cost_total = sum(m.get("cost_usd", 0.0) for m in metrics)

    def summary(key: str) -> dict[str, Any]:
        vals = [m.get(key) for m in metrics]
        return {"mean": _avg(vals), "n": sum(v is not None for v in vals)}

    return {
        "benchmarks_run": len(results),
        "benchmarks_errored": sum(1 for r in results if r.get("status") == "error"),
        "workflow_completion_rate": _avg([m["run_completed"] for m in metrics])
        if metrics
        else None,
        "task_completion_rate": summary("task_completion_rate"),
        "source_relevance": summary("source_relevance"),
        "evidence_coverage": summary("evidence_coverage"),
        "citation_correctness": summary("citation_correctness"),
        "unsupported_claim_rate": summary("unsupported_claim_rate"),
        "agent_failure_rate": summary("agent_failure_rate"),
        "latency_s": {
            "mean": _avg(latencies),
            "median": round(median(latencies), 2) if latencies else None,
            "p95": _pct(latencies, 0.95),
        },
        "tokens_per_run": _avg([m["total_tokens"] for m in metrics]),
        "cost_per_run_usd": round(cost_total / len(metrics), 6) if metrics else None,
        "cost_per_task_usd": round(cost_total / tasks_total, 6) if tasks_total else None,
        "pricing_known": pricing_known,
    }
