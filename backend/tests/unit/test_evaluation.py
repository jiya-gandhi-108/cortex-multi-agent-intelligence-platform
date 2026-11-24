"""Evaluation metrics, aggregation and review parsing (no database)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.benchmarks import BENCHMARKS, BY_ID, Benchmark, T
from app.evaluation.metrics import DEFINITIONS, aggregate, compute_metrics
from app.evaluation.report import ReviewImportError, parse_review_csv

BENCH = Benchmark(
    "x", "market_research", "objective", (T("pricing", "price"), T("regulation", "regulation"))
)


def _state(**overrides):
    state = {
        "status": "completed",
        "tasks": {"t1": {"status": "completed"}, "t2": {"status": "failed"}},
        "sources": {
            "Saaaaaa": {"title": "Price list", "content": "plans and price per user"},
            "Sbbbbbb": {"title": "Unrelated", "content": "weather today"},
        },
        "claims": {
            "C1": {"claim_id": "C1", "text": "The price is 10 per user"},
            "C2": {"claim_id": "C2", "text": "New regulation applies"},
        },
        "verdicts": {"C1": {"verdict": "supported"}, "C2": {"verdict": "unsupported"}},
        "verification": {
            "counts": {"total": 2, "unsupported": 1, "contradicted": 0},
            "evidence_score": 0.7,
        },
        "report": {
            "core": {
                "executive_summary": "Price [Saaaaaa].",
                "market_analysis": "",
                "competitor_analysis": "",
                "key_findings": [{"text": "x [Saaaaaa]", "citations": ["Saaaaaa"]}],
            },
            "strategy": {"opportunities": [], "risks": [], "recommendations": []},
            "validation": {"invalid_citations_removed": 1},
        },
        "task_stats": {"t1": {"rejected_citations": 2}},
        "executions": [
            {"status": "succeeded"},
            {"status": "failed"},
            {"status": "succeeded"},
            {"status": "succeeded"},
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.002},
    }
    state.update(overrides)
    return state


def test_compute_metrics_from_state() -> None:
    m = compute_metrics(_state(), BENCH, duration_s=12.345)
    assert m["run_completed"] == 1
    assert m["task_completion_rate"] == 0.5
    assert m["source_relevance"] == 0.5  # one of two sources mentions a topic keyword
    assert m["evidence_coverage"] == 0.5  # regulation claim was unsupported -> not covered
    assert m["topics_missing"] == ["regulation"]
    # 3 kept (inline x2 + list x1), 1 removed in report, 2 stripped at agents -> 3/6
    assert m["citations"] == {
        "kept": 3,
        "out_of_set": 0,
        "removed_in_report": 1,
        "stripped_at_agents": 2,
    }
    assert m["citation_correctness"] == 0.5
    assert m["unsupported_claim_rate"] == 0.5
    assert m["agent_failure_rate"] == 0.25
    assert m["latency_s"] == 12.35 and m["total_tokens"] == 150


def test_metrics_never_default_to_favourable_values() -> None:
    m = compute_metrics({"status": "failed", "error": {"code": "budget_exceeded"}}, BENCH, 1.0)
    assert m["run_completed"] == 0 and m["error_code"] == "budget_exceeded"
    for key in (
        "task_completion_rate",
        "source_relevance",
        "citation_correctness",
        "unsupported_claim_rate",
    ):
        assert m[key] is None  # no data -> n/a, not 0% or 100%
    assert m["evidence_coverage"] == 0.0


def test_out_of_set_citations_are_counted_not_hidden() -> None:
    state = _state()
    state["report"]["core"]["executive_summary"] = "Bad [Sdeadbe]."
    m = compute_metrics(state, BENCH, 1.0)
    assert m["citations"]["out_of_set"] == 1


def test_aggregate() -> None:
    results = [
        {"status": "completed", "metrics": compute_metrics(_state(), BENCH, 10.0)},
        {"status": "failed", "metrics": compute_metrics({"status": "failed"}, BENCH, 30.0)},
    ]
    s = aggregate(results, pricing_known=False)
    assert s["workflow_completion_rate"] == 0.5
    assert s["task_completion_rate"] == {"mean": 0.5, "n": 1}  # n shows how many contributed
    assert s["latency_s"]["mean"] == 20.0
    assert s["pricing_known"] is False


def test_benchmark_suite_shape() -> None:
    assert len(BENCHMARKS) >= 15 and len(BY_ID) == len(BENCHMARKS)
    assert len({b.domain for b in BENCHMARKS}) == 7  # every research domain in the brief
    assert all(len(b.topics) >= 3 and len(b.objective) >= 80 for b in BENCHMARKS)
    assert set(DEFINITIONS) >= {
        "citation_correctness",
        "unsupported_claim_rate",
        "agent_failure_rate",
    }


def test_review_csv_validation(tmp_path: Path) -> None:
    good = tmp_path / "r.csv"
    good.write_text(
        "benchmark_id,source_relevance_1to5,report_usefulness_1to5,factual_spot_check,reviewer,notes\n"
        "b01,4,5,pass,ana,solid\nb02,,,,,\n"
    )
    assert parse_review_csv(good) == {
        "b01": {
            "source_relevance_1to5": "4",
            "report_usefulness_1to5": "5",
            "factual_spot_check": "pass",
            "reviewer": "ana",
            "notes": "solid",
        }
    }  # untouched rows are skipped
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "benchmark_id,source_relevance_1to5,report_usefulness_1to5,factual_spot_check\nb01,9,3,pass\n"
    )
    with pytest.raises(ReviewImportError, match="1-5"):
        parse_review_csv(bad)
