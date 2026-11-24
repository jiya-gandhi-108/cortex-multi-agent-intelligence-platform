"""Run the benchmark suite and store results.

Modes
  live     real LLM + web search (needs keys; costs tokens). The only mode whose
           numbers describe research quality.
  offline  scripted agents from the test suite. Exercises the whole workflow and
           this harness end to end; its numbers describe the fakes, not Cortex's
           research quality, and every report produced in this mode says so.

Runs are sequential (provider rate limits), resumable (completed benchmarks in a
run are skipped), and each benchmark is isolated: an exception becomes an
``error`` result, never a crash of the suite.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.agents.base import RunContext
from app.agents.profiles import profile_for
from app.core.config import Settings
from app.core.logging import get_logger
from app.database.session import session_scope
from app.evaluation.benchmarks import BENCHMARKS, Benchmark
from app.evaluation.metrics import aggregate, compute_metrics
from app.graph.builder import RECURSION_LIMIT, build_research_graph
from app.graph.state import initial_state
from app.llm.base import LLMClient
from app.models import EvaluationResult, EvaluationRun
from app.monitoring.cost import BudgetTracker, parse_pricing
from app.tools.evidence import EvidenceCollector

log = get_logger(__name__)

LLMFactory = Callable[[], LLMClient]
CollectorFactory = Callable[[], Any]


def git_commit() -> str | None:
    if os.environ.get("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"][:40]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def live_factories(settings: Settings) -> tuple[LLMFactory, CollectorFactory]:
    from app.llm.factory import get_llm_client
    from app.tools.fetcher import SafeFetcher
    from app.tools.search import get_search_provider

    def collector() -> EvidenceCollector:
        return EvidenceCollector(settings, get_search_provider(settings), SafeFetcher(settings))

    return (lambda: get_llm_client(settings)), collector


def offline_factories() -> tuple[LLMFactory, CollectorFactory]:
    import importlib

    try:  # imported dynamically: production images ship without tests/
        fakes = importlib.import_module("tests.fakes")
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("offline mode needs the backend test suite (tests/fakes.py)") from exc
    return fakes.FakeLLM, fakes.FakeCollector


class EvaluationRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        mode: str,
        depth: str = "quick",
        llm_factory: LLMFactory,
        collector_factory: CollectorFactory,
        max_research_iterations: int = 1,
    ) -> None:
        if mode not in ("live", "offline"):
            raise ValueError("mode must be 'live' or 'offline'")
        self.settings = settings
        self.mode = mode
        self.depth = depth
        self.llm_factory = llm_factory
        self.collector_factory = collector_factory
        self.max_iterations = max_research_iterations
        profile_for(depth)  # validates depth

    def _models(self) -> dict[str, Any]:
        if self.mode == "offline":
            return {"note": "scripted agents (tests/fakes.py)"}
        s = self.settings
        return {
            "provider": s.default_llm_provider.value,
            "reasoning": s.groq_model_reasoning,
            "fast": s.groq_model_fast,
            "search": s.search_provider.value,
        }

    async def create_run(self) -> uuid.UUID:
        async with session_scope() as session:
            run = EvaluationRun(
                mode=self.mode,
                provider="scripted"
                if self.mode == "offline"
                else self.settings.default_llm_provider.value,
                models=self._models(),
                depth=self.depth,
                git_commit=git_commit(),
                status="running",
            )
            session.add(run)
            await session.flush()
            return run.id

    async def run_benchmark(self, benchmark: Benchmark) -> dict[str, Any]:
        collector = self.collector_factory()
        ctx = RunContext(
            project_id=f"eval:{benchmark.id}",
            settings=self.settings,
            llm=self.llm_factory(),
            collector=collector,
            budget=BudgetTracker(
                self.settings.max_tokens_per_project,
                Decimal(str(self.settings.max_cost_usd_per_project)),
            ),
        )
        limits = {
            "max_research_iterations": self.max_iterations,
            "require_plan_approval": False,
            "max_agent_retries": self.settings.max_agent_retries,
        }
        graph = build_research_graph(InMemorySaver())
        start = time.perf_counter()
        try:
            state = await graph.ainvoke(
                initial_state(
                    project_id=ctx.project_id,
                    title=benchmark.id,
                    objective=benchmark.objective,
                    depth=self.depth,
                    focus_areas=list(benchmark.focus_areas),
                    limits=limits,
                ),
                {
                    "configurable": {"thread_id": uuid.uuid4().hex},
                    "recursion_limit": RECURSION_LIMIT,
                    "run_name": f"eval:{benchmark.id}",
                    "tags": ["cortex", "evaluation", self.mode],
                },
                context=ctx,
            )
        except Exception as exc:  # isolate: one broken benchmark never sinks the suite
            log.exception("benchmark_error", benchmark=benchmark.id)
            return {
                "benchmark_id": benchmark.id,
                "status": "error",
                "duration_s": time.perf_counter() - start,
                "metrics": {},
                "error": f"{type(exc).__name__}: {exc}"[:2000],
                "report": None,
            }
        finally:
            for closer in (
                getattr(getattr(collector, "fetcher", None), "aclose", None),
                getattr(getattr(collector, "search", None), "aclose", None),
            ):
                if closer:
                    await closer()
        duration = time.perf_counter() - start
        metrics = compute_metrics(dict(state), benchmark, duration)
        return {
            "benchmark_id": benchmark.id,
            "status": state.get("status", "unknown"),
            "duration_s": duration,
            "metrics": metrics,
            "error": (state.get("error") or {}).get("message"),
            "report": (state.get("report") or {}).get("markdown"),
        }

    async def run(
        self, benchmarks: Sequence[Benchmark] = BENCHMARKS, run_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        run_id = run_id or await self.create_run()
        async with session_scope() as session:
            done = set(
                (
                    await session.execute(
                        select(EvaluationResult.benchmark_id).where(
                            EvaluationResult.run_id == run_id
                        )
                    )
                ).scalars()
            )
        for benchmark in benchmarks:
            if benchmark.id in done:
                log.info("benchmark_skipped_already_done", benchmark=benchmark.id)
                continue
            log.info("benchmark_started", benchmark=benchmark.id, mode=self.mode)
            outcome = await self.run_benchmark(benchmark)
            m = outcome["metrics"]
            async with session_scope() as session:
                session.add(
                    EvaluationResult(
                        run_id=run_id,
                        benchmark_id=benchmark.id,
                        status=outcome["status"],
                        duration_s=round(outcome["duration_s"], 3),
                        total_tokens=m.get("total_tokens", 0),
                        estimated_cost=Decimal(str(m.get("cost_usd", 0))),
                        metrics=m,
                        error=outcome["error"],
                        report_markdown=outcome["report"],
                    )
                )
            log.info(
                "benchmark_finished",
                benchmark=benchmark.id,
                status=outcome["status"],
                seconds=round(outcome["duration_s"], 1),
            )
        await self.finalize(run_id)
        return run_id

    async def finalize(self, run_id: uuid.UUID) -> dict[str, Any]:
        async with session_scope() as session:
            run = await session.get(EvaluationRun, run_id)
            assert run is not None
            rows = (
                (
                    await session.execute(
                        select(EvaluationResult).where(EvaluationResult.run_id == run_id)
                    )
                )
                .scalars()
                .all()
            )
            pricing = parse_pricing(self.settings.llm_pricing_json)
            pricing_known = self.mode == "live" and all(
                m in pricing
                for m in (self.settings.groq_model_reasoning, self.settings.groq_model_fast)
            )
            summary = aggregate(
                [{"status": r.status, "metrics": r.metrics} for r in rows],
                pricing_known=pricing_known,
            )
            run.summary = summary
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            return summary
