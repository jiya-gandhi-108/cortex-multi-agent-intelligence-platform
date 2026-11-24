"""Offline evaluation run through the real database: store, report, resume, review import."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database.session import session_scope
from app.evaluation.benchmarks import BY_ID
from app.evaluation.report import ReviewImportError, import_review, load_run, write_report
from app.evaluation.runner import EvaluationRunner, offline_factories
from app.models import EvaluationResult
from tests.conftest import make_settings

pytestmark = pytest.mark.integration


async def test_offline_run_store_report_resume_and_review(db_engine: None, tmp_path: Path) -> None:
    llm, collector = offline_factories()
    runner = EvaluationRunner(
        make_settings(), mode="offline", llm_factory=llm, collector_factory=collector
    )
    picked = [BY_ID["b01-india-healthcare-saas"], BY_ID["b02-eu-ev-charging"]]
    run_id = await runner.run(picked[:1])
    await runner.run(picked, run_id=run_id)  # resume: b01 skipped, b02 added

    async with session_scope() as s:
        count = (
            await s.execute(select(func.count()).where(EvaluationResult.run_id == run_id))
        ).scalar_one()
    assert count == 2
    run, rows = await load_run(run_id)
    assert run.status == "completed" and run.summary["benchmarks_run"] == 2
    assert {r.benchmark_id for r in rows} == {"b01-india-healthcare-saas", "b02-eu-ev-charging"}
    assert all(r.metrics["run_completed"] == 1 and r.report_markdown for r in rows)

    out = await write_report(run_id, tmp_path)
    report = (out / "report.md").read_text()
    assert "Harness check, not a quality measurement" in report
    assert "| Workflow completion rate | 100.0% | 2 |" in report
    assert (out / "reports" / "b01-india-healthcare-saas.md").exists()

    review = out / "human_review.csv"
    review.write_text(
        review.read_text().replace(
            "b01-india-healthcare-saas,,,,,", "b01-india-healthcare-saas,4,3,partial,qa,"
        )
    )
    assert await import_review(run_id, review) == 1
    out = await write_report(run_id, tmp_path)
    assert "Factual spot checks: 0 pass, 1 partial" in (out / "report.md").read_text()

    other = tmp_path / "other.csv"
    other.write_text(
        "benchmark_id,source_relevance_1to5,report_usefulness_1to5,factual_spot_check\nb99,3,3,pass\n"
    )
    with pytest.raises(ReviewImportError, match="not in this run"):
        await import_review(run_id, other)


async def test_benchmark_exception_becomes_error_result(db_engine: None) -> None:
    class Exploding:
        provider = "boom"

        def model_for(self, tier):
            return "m"

        async def complete_structured(self, request):
            raise RuntimeError("provider exploded")

    _, collector = offline_factories()
    runner = EvaluationRunner(
        make_settings(), mode="offline", llm_factory=Exploding, collector_factory=collector
    )
    run_id = await runner.run([BY_ID["b03-vector-databases"]])
    _, rows = await load_run(run_id)
    assert rows[0].status in ("failed", "error")  # contained by the workflow or the harness
    assert rows[0].metrics.get("run_completed", 0) == 0
