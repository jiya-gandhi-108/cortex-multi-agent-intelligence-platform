"""Run manager: executes research workflows in the background and persists them.

* One asyncio task per active run, inside the API process (see architecture.md
  for why there is no separate queue at this scale).
* LangGraph's Postgres checkpointer makes runs durable: thread id
  ``{project_id}:{run}``. Pausing for approval, resuming after a restart and
  re-running all go through the same mechanism.
* Each graph update (``stream_mode="updates"``) is persisted in its own
  transaction, so the database always reflects completed steps and the
  graph itself never touches the database.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from redis.asyncio import Redis

from app.agents.base import RunContext, WorkflowCancelledError
from app.core.config import LLMProvider, Settings
from app.core.logging import get_logger
from app.database.session import session_scope
from app.graph.builder import RECURSION_LIMIT, build_research_graph
from app.graph.events import EventType, WorkflowEvent
from app.graph.state import initial_state
from app.llm.base import LLMClient
from app.llm.factory import get_llm_client
from app.models.enums import ProjectStatus
from app.monitoring.cost import BudgetTracker, TokenUsage
from app.repositories.runs import RunRepository
from app.services.events import RedisEventSink
from app.tools.evidence import EvidenceCollector
from app.tools.fetcher import SafeFetcher
from app.tools.search import get_search_provider

log = get_logger(__name__)

STAGE_STATUS = {
    "planning": ProjectStatus.PLANNING,
    "planned": ProjectStatus.PLANNING,
    "approved": ProjectStatus.RUNNING,
    "researching": ProjectStatus.RUNNING,
    "collecting": ProjectStatus.RUNNING,
    "follow_up_research": ProjectStatus.RUNNING,
    "verifying": ProjectStatus.VERIFYING,
    "verified": ProjectStatus.VERIFYING,
    "reporting": ProjectStatus.REPORTING,
    "reported": ProjectStatus.REPORTING,
    "quality_checked": ProjectStatus.REPORTING,
}
INTERRUPTED_MESSAGE = "Run was interrupted by a server shutdown; start it again to resume."
AGENT_NODES = {"research_agent", "competitor_agent", "market_analysis_agent"}

LLMFactory = Callable[[Settings, str], LLMClient]
CollectorFactory = Callable[[Settings], EvidenceCollector]


def default_llm_factory(settings: Settings, provider: str) -> LLMClient:
    return get_llm_client(settings, provider)


def default_collector_factory(settings: Settings) -> EvidenceCollector:
    return EvidenceCollector(settings, get_search_provider(settings), SafeFetcher(settings))


def thread_id(project_id: uuid.UUID, run: int) -> str:
    return f"{project_id}:{run}"


@dataclass
class RunHandle:
    project_id: uuid.UUID
    run: int
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    shutting_down: bool = False


class ResearchRunner:
    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        checkpointer: BaseCheckpointSaver[Any],
        llm_factory: LLMFactory = default_llm_factory,
        collector_factory: CollectorFactory = default_collector_factory,
        cancel_grace_seconds: float = 5.0,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.graph = build_research_graph(checkpointer)
        self.llm_factory = llm_factory
        self.collector_factory = collector_factory
        self.cancel_grace_seconds = cancel_grace_seconds
        self._runs: dict[uuid.UUID, RunHandle] = {}

    # ----------------------------------------------------------- public API

    def is_running(self, project_id: uuid.UUID) -> bool:
        handle = self._runs.get(project_id)
        return bool(handle and handle.task and not handle.task.done())

    def sink(self, project_id: uuid.UUID, run: int) -> RedisEventSink:
        return RedisEventSink(
            self.redis, str(project_id), run, self.settings.event_stream_ttl_hours
        )

    def launch(self, project_id: uuid.UUID, run: int, payload: Any) -> None:
        """Start (or resume) a run in the background. Caller has committed the DB state."""
        if self.is_running(project_id):
            raise RuntimeError(f"run already active for {project_id}")
        handle = RunHandle(project_id, run)
        handle.task = asyncio.create_task(self._run(handle, payload), name=f"research:{project_id}")
        self._runs[project_id] = handle

    def fresh_input(self, project: Any) -> Any:
        return initial_state(
            project_id=str(project.id),
            title=project.title,
            objective=project.objective,
            depth=project.depth.value,
            focus_areas=list(project.focus_areas or []),
            limits=dict(project.config or {}),
        )

    @staticmethod
    def approval_input(approved: bool, reason: str | None) -> Any:
        return Command(resume={"approved": approved, "reason": reason})

    async def cancel(self, project_id: uuid.UUID) -> bool:
        handle = self._runs.get(project_id)
        if not handle or not handle.task or handle.task.done():
            return False
        handle.cancel_event.set()  # graceful: stops at the next node / LLM call
        task = handle.task
        asyncio.get_running_loop().call_later(
            self.cancel_grace_seconds, lambda: task.done() or task.cancel()
        )
        return True

    async def wait(self, project_id: uuid.UUID, seconds: float = 30.0) -> None:
        """Await a run's completion (used by tests and graceful operations)."""
        handle = self._runs.get(project_id)
        if handle and handle.task:
            await asyncio.wait_for(asyncio.shield(handle.task), seconds)

    async def shutdown(self) -> None:
        """Stop runs on process exit; they are marked interrupted and can be resumed."""
        handles = [h for h in self._runs.values() if h.task and not h.task.done()]
        for h in handles:
            h.shutting_down = True
            assert h.task is not None
            h.task.cancel()
        await asyncio.gather(*(h.task for h in handles if h.task), return_exceptions=True)

    async def recover_orphans(self) -> list[uuid.UUID]:
        async with session_scope() as session:
            ids = await RunRepository(session).mark_orphans_interrupted()
        if ids:
            log.warning("runs_marked_interrupted", count=len(ids))
        return ids

    # ------------------------------------------------------------ execution

    async def _run(self, handle: RunHandle, payload: Any) -> None:
        pid, run = handle.project_id, handle.run
        sink = self.sink(pid, run)
        collector: EvidenceCollector | None = None
        config = RunnableConfig(
            configurable={"thread_id": thread_id(pid, run)},
            recursion_limit=RECURSION_LIMIT,
            # Shown in LangSmith: filter traces by project or run.
            run_name=f"research:{pid}:{run}",
            tags=["cortex", "research"],
            metadata={"project_id": str(pid), "run": run},
        )
        try:
            async with session_scope() as session:
                project = await RunRepository(session).project(pid)
                limits = dict(project.config or {})
                provider = limits.get("provider") or self.settings.default_llm_provider.value
                used = TokenUsage(project.total_tokens, 0, project.estimated_cost)
            budget = BudgetTracker(
                max_tokens=int(limits.get("max_tokens", self.settings.max_tokens_per_project)),
                max_cost_usd=Decimal(
                    str(limits.get("max_cost_usd", self.settings.max_cost_usd_per_project))
                ),
                used=used,  # resumed runs keep counting against the same budget
            )
            collector = self.collector_factory(self.settings)
            ctx = RunContext(
                project_id=str(pid),
                settings=self.settings,
                llm=self.llm_factory(self.settings, provider),
                collector=collector,
                budget=budget,
                sink=sink,
                cancel_event=handle.cancel_event,
            )
            await self._set_status(pid, run, ProjectStatus.PLANNING, stage="starting")

            async for chunk in self.graph.astream(
                payload, config, context=ctx, stream_mode="updates"
            ):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        await self._set_status(
                            pid, run, ProjectStatus.AWAITING_APPROVAL, stage="awaiting_approval"
                        )
                    elif isinstance(update, dict):
                        log.info(
                            "graph_node_completed",
                            project_id=str(pid),
                            run=run,
                            node=node,
                            stage=update.get("stage"),
                            error=(update.get("error") or {}).get("code"),
                        )
                        await self._persist(pid, node, update)

            snapshot = await self.graph.aget_state(config)
            if snapshot.next:  # paused (approval gate)
                return
            await self._finalize(pid, run, snapshot.values)
        except (WorkflowCancelledError, asyncio.CancelledError):
            if handle.shutting_down:
                await self._set_status(
                    pid,
                    run,
                    ProjectStatus.INTERRUPTED,
                    stage="interrupted",
                    error=INTERRUPTED_MESSAGE,
                )
            else:
                await self._set_status(
                    pid,
                    run,
                    ProjectStatus.CANCELLED,
                    stage="cancelled",
                    error="Cancelled by user",
                    completed=True,
                )
        except Exception as exc:
            log.exception("run_crashed", project_id=str(pid))
            await self._set_status(
                pid,
                run,
                ProjectStatus.FAILED,
                stage="failed",
                error=f"internal_error: {type(exc).__name__}",
                completed=True,
            )
        finally:
            self._runs.pop(pid, None)
            if collector is not None:
                for closer in (
                    getattr(getattr(collector, "fetcher", None), "aclose", None),
                    getattr(getattr(collector, "search", None), "aclose", None),
                ):
                    if closer:
                        try:
                            await closer()
                        except Exception:  # pragma: no cover - best-effort cleanup
                            log.warning("collector_close_failed")

    async def _persist(self, pid: uuid.UUID, node: str, update: dict[str, Any]) -> None:
        async with session_scope() as session:
            repo = RunRepository(session)
            project = await repo.project(pid, for_update=True)
            summary = dict(project.run_summary or {})

            if update.get("plan"):
                project.plan = update["plan"]
            if update.get("tasks"):
                await repo.upsert_tasks(
                    pid, update["tasks"], update.get("outputs"), update.get("task_stats")
                )
            if update.get("sources") or update.get("executions"):
                ids = await repo.task_ids(pid)
                if update.get("sources"):
                    await repo.insert_sources(pid, update["sources"], ids)
                if update.get("executions"):
                    await repo.insert_executions(pid, update["executions"], ids)
            usage = update.get("usage") or {}
            if usage:
                project.total_tokens += usage.get("prompt_tokens", 0) + usage.get(
                    "completion_tokens", 0
                )
                project.estimated_cost += Decimal(str(usage.get("cost_usd", 0)))
            if update.get("verification"):
                v = update["verification"]
                summary["verification"] = {
                    k: v[k]
                    for k in (
                        "evidence_score",
                        "reliability_score",
                        "sufficient",
                        "reasons",
                        "counts",
                        "criteria",
                        "outdated_sources",
                        "sources_needing_verification",
                        "contradictions",
                    )
                }
                summary["verification_full"] = v
            if update.get("report"):
                summary["report_version"] = await repo.add_report(
                    pid, update["report"], summary.get("verification_full")
                )
            if update.get("quality"):
                summary["quality"] = update["quality"]
                await repo.set_report_quality(pid, update["quality"])
            if "iteration" in update:
                summary["iterations"] = update["iteration"]

            stage = update.get("stage")
            status = STAGE_STATUS.get(stage or "")
            if status is None and node in AGENT_NODES:
                status = ProjectStatus.RUNNING
            if stage:
                project.current_stage = stage
            if status and not project.status.is_terminal:
                project.status = status
            project.run_summary = summary

    async def _finalize(self, pid: uuid.UUID, run: int, values: dict[str, Any]) -> None:
        outcome = values.get("status")
        status = {
            "completed": ProjectStatus.COMPLETED,
            "cancelled": ProjectStatus.CANCELLED,
        }.get(str(outcome), ProjectStatus.FAILED)
        error = values.get("error")
        message = f"{error['code']}: {error['message']}" if error else None
        async with session_scope() as session:
            project = await RunRepository(session).project(pid, for_update=True)
            summary = dict(project.run_summary or {})
            summary["usage"] = values.get("usage", {})
            summary["warnings"] = (values.get("quality") or {}).get("warnings", [])
            summary["error"] = error
            project.run_summary = summary
        await self._set_status(
            pid, run, status, stage=str(outcome or "failed"), error=message, completed=True
        )

    async def _set_status(
        self,
        pid: uuid.UUID,
        run: int,
        status: ProjectStatus,
        *,
        stage: str | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        async with session_scope() as session:
            project = await RunRepository(session).project(pid, for_update=True)
            project.status = status
            if stage:
                project.current_stage = stage
            if error is not None:
                project.error_message = error
            if completed:
                project.completed_at = datetime.now(UTC)
        await self.sink(pid, run).emit(
            WorkflowEvent(
                type=EventType.STATUS,
                project_id=str(pid),
                stage=stage,
                data={"status": status.value, "error": error},
            )
        )


def provider_preflight(settings: Settings, provider: str | None) -> list[str]:
    """Names of missing integrations for a run; empty if the run can start."""
    missing = []
    chosen = LLMProvider(provider) if provider else settings.default_llm_provider
    if not settings.provider_configured(chosen):
        missing.append(f"{chosen.value.upper()}_API_KEY")
    if not settings.search_configured():
        missing.append(f"{settings.search_provider.value.upper()}_API_KEY")
    return missing
