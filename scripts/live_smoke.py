"""Run one real research workflow against live Groq + search APIs.

Costs real API credits (small: depth=quick). Requires GROQ_API_KEY and the key
for SEARCH_PROVIDER in backend/.env. Writes the report to ./smoke_report.md.

Usage (from backend/):
    .venv/bin/python ../scripts/live_smoke.py "Research the Indian healthcare SaaS market ..."
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from langgraph.checkpoint.memory import InMemorySaver

from app.agents.base import RunContext
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.graph.builder import RECURSION_LIMIT, build_research_graph
from app.graph.events import WorkflowEvent
from app.graph.state import initial_state
from app.llm.factory import get_llm_client
from app.monitoring.cost import BudgetTracker
from app.tools.evidence import EvidenceCollector
from app.tools.fetcher import SafeFetcher
from app.tools.search import get_search_provider


class PrintSink:
    async def emit(self, event: WorkflowEvent) -> None:
        label = event.stage or event.type.value
        detail = event.task_key or ""
        if event.type.value == "llm_call":
            d = event.data
            detail += f" {d.get('model')} {d.get('prompt_tokens')}+{d.get('completion_tokens')} tok"
        elif event.type.value == "error":
            detail += f" {event.data}"
        print(f"  [{event.at:%H:%M:%S}] {event.type.value:<16} {label:<20} {detail}")


async def main(objective: str) -> int:
    settings = get_settings()
    configure_logging("WARNING")
    status = settings.integration_status()
    if not status["groq"] or not settings.search_configured():
        print("Missing keys:", {k: v for k, v in status.items() if not v})
        return 2

    fetcher = SafeFetcher(settings)
    ctx = RunContext(
        project_id="smoke",
        settings=settings,
        llm=get_llm_client(settings),
        collector=EvidenceCollector(settings, get_search_provider(settings), fetcher),
        budget=BudgetTracker(
            settings.max_tokens_per_project, Decimal(str(settings.max_cost_usd_per_project))
        ),
        sink=PrintSink(),
    )
    limits = {"max_research_iterations": 1, "max_agent_retries": 1, "require_plan_approval": False}
    graph = build_research_graph(InMemorySaver())
    start = time.perf_counter()
    try:
        state = await graph.ainvoke(
            initial_state(
                project_id="smoke",
                title="Smoke test report",
                objective=objective,
                depth="quick",
                focus_areas=[],
                limits=limits,
            ),
            {"configurable": {"thread_id": uuid.uuid4().hex}, "recursion_limit": RECURSION_LIMIT},
            context=ctx,
        )
    finally:
        await fetcher.aclose()
    elapsed = time.perf_counter() - start
    print(f"\nstatus={state['status']} error={state.get('error')} elapsed={elapsed:.0f}s")
    print(f"usage={state.get('usage')}  sources={len(state.get('sources', {}))}")
    if state.get("verification"):
        v = state["verification"]
        print(f"evidence_score={v['evidence_score']} reliability={v['reliability_score']}")
        print(f"claims={v['counts']}")
    if state.get("report"):
        await asyncio.to_thread(Path("smoke_report.md").write_text, state["report"]["markdown"])
        print("report written to smoke_report.md")
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    goal = " ".join(sys.argv[1:]) or (
        "Research the Indian healthcare SaaS market, identify major competitors and their "
        "pricing, and recommend business opportunities."
    )
    raise SystemExit(asyncio.run(main(goal)))
