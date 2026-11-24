"""Evaluation report (Markdown + JSON) and the human-review loop (CSV out, CSV in)."""

from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import select

from app.database.session import session_scope
from app.evaluation.benchmarks import BY_ID
from app.evaluation.metrics import DEFINITIONS
from app.models import EvaluationResult, EvaluationRun

REVIEW_FIELDS = [
    "benchmark_id",
    "source_relevance_1to5",
    "report_usefulness_1to5",
    "factual_spot_check",  # pass | partial | fail: verify 3 cited claims against their sources
    "reviewer",
    "notes",
]
OFFLINE_BANNER = (
    "> **Harness check, not a quality measurement.** This run used scripted agents "
    "(`tests/fakes.py`) instead of a real LLM and web search. It proves the workflow and the "
    "evaluation pipeline work end to end; the numbers below describe the fakes. Run "
    "`python -m app.evaluation run --mode live` for real results."
)


def _fmt(value: Any, pct: bool = True) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and pct:
        return f"{value * 100:.1f}%"
    return str(value)


async def load_run(run_id: uuid.UUID) -> tuple[EvaluationRun, list[EvaluationResult]]:
    async with session_scope() as session:
        run = await session.get(EvaluationRun, run_id)
        if run is None:
            raise SystemExit(f"evaluation run {run_id} not found")
        rows = list(
            (
                await session.execute(
                    select(EvaluationResult)
                    .where(EvaluationResult.run_id == run_id)
                    .order_by(EvaluationResult.benchmark_id)
                )
            ).scalars()
        )
        session.expunge_all()
        return run, rows


def render_markdown(run: EvaluationRun, rows: list[EvaluationResult]) -> str:
    s = run.summary or {}
    lines = [f"# Cortex evaluation report: run `{str(run.id)[:8]}`", ""]
    if run.mode == "offline":
        lines += [OFFLINE_BANNER, ""]
    lines += [
        f"- Mode: **{run.mode}**, depth `{run.depth}`, started {run.created_at:%Y-%m-%d %H:%M UTC}",
        f"- Models: `{json.dumps(run.models)}`",
        f"- Code version: `{run.git_commit or 'unknown'}`",
        f"- Benchmarks run: {s.get('benchmarks_run', len(rows))} "
        f"(errored: {s.get('benchmarks_errored', 0)})",
        "",
        "## Summary",
        "",
        "| Metric | Value | n |",
        "|---|---|---|",
        f"| Workflow completion rate | {_fmt(s.get('workflow_completion_rate'))} | {len(rows)} |",
    ]
    for key, label in [
        ("task_completion_rate", "Task completion rate"),
        ("source_relevance", "Source relevance (lexical proxy)"),
        ("evidence_coverage", "Evidence coverage"),
        ("citation_correctness", "Citation correctness"),
        ("unsupported_claim_rate", "Unsupported claim rate"),
        ("agent_failure_rate", "Agent execution failure rate"),
    ]:
        item = s.get(key) or {}
        lines.append(f"| {label} | {_fmt(item.get('mean'))} | {item.get('n', 0)} |")
    lat = s.get("latency_s") or {}
    lines += [
        f"| Workflow latency (mean / median / p95) | {_fmt(lat.get('mean'), False)} s / "
        f"{_fmt(lat.get('median'), False)} s / {_fmt(lat.get('p95'), False)} s | {len(rows)} |",
        f"| Tokens per run (mean) | {_fmt(s.get('tokens_per_run'), False)} | {len(rows)} |",
        "| Cost per run / per task | "
        + (
            f"${s.get('cost_per_run_usd')} / ${s.get('cost_per_task_usd')}"
            if s.get("pricing_known")
            else "unknown (no provider pricing configured)"
        )
        + f" | {len(rows)} |",
        "",
        "## Per benchmark",
        "",
        "| Benchmark | Domain | Outcome | Tasks done | Coverage | Citations | Unsupported | Failures | Latency | Tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        m = r.metrics or {}
        b = BY_ID.get(r.benchmark_id)
        outcome = r.status if not m.get("error_code") else f"{r.status} ({m['error_code']})"
        lines.append(
            f"| {r.benchmark_id} | {b.domain if b else '?'} | {outcome} | {_fmt(m.get('task_completion_rate'))} | "
            f"{_fmt(m.get('evidence_coverage'))} | {_fmt(m.get('citation_correctness'))} | "
            f"{_fmt(m.get('unsupported_claim_rate'))} | {_fmt(m.get('agent_failure_rate'))} | "
            f"{r.duration_s:.1f} s | {r.total_tokens} |"
        )
    missing: list[tuple[str, list[str]]] = [
        (r.benchmark_id, list(r.metrics.get("topics_missing") or []))
        for r in rows
        if r.metrics.get("topics_missing")
    ]
    if missing:
        lines += ["", "### Topics without verified evidence", ""]
        lines += [f"- `{bid}`: {', '.join(topics)}" for bid, topics in missing]
    errors = [(r.benchmark_id, r.error) for r in rows if r.error]
    if errors:
        lines += ["", "### Errors and failure reasons", ""]
        lines += [f"- `{bid}`: {err}" for bid, err in errors]

    reviews: list[dict[str, Any]] = [r.human_review for r in rows if r.human_review]
    lines += ["", "## Human review", ""]
    if reviews:

        def avg(field: str) -> str:
            vals = [float(rv[field]) for rv in reviews if str(rv.get(field, "")).strip()]
            return f"{mean(vals):.2f} / 5 (n={len(vals)})" if vals else "n/a"

        checks = [rv.get("factual_spot_check", "") for rv in reviews]
        lines += [
            f"- Source relevance: {avg('source_relevance_1to5')}",
            f"- Report usefulness: {avg('report_usefulness_1to5')}",
            f"- Factual spot checks: {checks.count('pass')} pass, {checks.count('partial')} partial, "
            f"{checks.count('fail')} fail (of {len(checks)})",
        ]
    else:
        lines += [
            "Not yet reviewed. Fill in `human_review.csv` and import it with "
            "`python -m app.evaluation import-review <run_id> human_review.csv`."
        ]

    lines += ["", "## Metric definitions", ""]
    lines += [f"- **{k}**: {v}" for k, v in DEFINITIONS.items()]
    return "\n".join(lines) + "\n"


async def write_report(run_id: uuid.UUID, out_dir: Path) -> Path:
    run, rows = await load_run(run_id)
    target = out_dir / str(run_id)
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.md").write_text(render_markdown(run, rows))
    (target / "results.json").write_text(
        json.dumps(
            {
                "run": {
                    "id": str(run.id),
                    "mode": run.mode,
                    "depth": run.depth,
                    "models": run.models,
                    "git_commit": run.git_commit,
                    "created_at": run.created_at.isoformat(),
                    "summary": run.summary,
                },
                "results": [
                    {
                        "benchmark_id": r.benchmark_id,
                        "status": r.status,
                        "duration_s": r.duration_s,
                        "metrics": r.metrics,
                        "error": r.error,
                        "human_review": r.human_review,
                    }
                    for r in rows
                ],
            },
            indent=2,
            default=str,
        )
    )
    with (target / "human_review.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "benchmark_id": r.benchmark_id,
                    **{k: (r.human_review or {}).get(k, "") for k in REVIEW_FIELDS[1:]},
                }
            )
    reports = target / "reports"
    reports.mkdir(exist_ok=True)
    for r in rows:
        if r.report_markdown:
            (reports / f"{r.benchmark_id}.md").write_text(r.report_markdown)
    return target


class ReviewImportError(ValueError):
    pass


def parse_review_csv(path: Path) -> dict[str, dict[str, str]]:
    reviews: dict[str, dict[str, str]] = {}
    with path.open(newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            bid = (row.get("benchmark_id") or "").strip()
            if not bid:
                continue
            entry = {k: (row.get(k) or "").strip() for k in REVIEW_FIELDS[1:]}
            if not any(
                entry[k]
                for k in ("source_relevance_1to5", "report_usefulness_1to5", "factual_spot_check")
            ):
                continue  # untouched row
            for field in ("source_relevance_1to5", "report_usefulness_1to5"):
                if entry[field] and entry[field] not in {"1", "2", "3", "4", "5"}:
                    raise ReviewImportError(f"line {i}: {field} must be 1-5, got {entry[field]!r}")
            if entry["factual_spot_check"] and entry["factual_spot_check"] not in {
                "pass",
                "partial",
                "fail",
            }:
                raise ReviewImportError(
                    f"line {i}: factual_spot_check must be pass, partial or fail"
                )
            reviews[bid] = entry
    return reviews


async def import_review(run_id: uuid.UUID, path: Path) -> int:
    reviews = parse_review_csv(path)
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(EvaluationResult).where(EvaluationResult.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        known = {r.benchmark_id: r for r in rows}
        unknown = sorted(set(reviews) - set(known))
        if unknown:
            raise ReviewImportError(f"benchmarks not in this run: {', '.join(unknown)}")
        for bid, review in reviews.items():
            known[bid].human_review = review
    return len(reviews)
