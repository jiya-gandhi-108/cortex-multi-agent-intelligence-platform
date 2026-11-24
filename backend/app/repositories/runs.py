"""Persistence of workflow output: tasks, sources, executions, reports.

Called only by the run manager, after the owning project has been resolved,
so these methods take a project id rather than re-checking ownership.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentExecution, ResearchProject, ResearchReport, ResearchSource, ResearchTask
from app.models.enums import ExecutionStatus, ProjectStatus, TaskPriority, TaskStatus


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def project(self, project_id: uuid.UUID, *, for_update: bool = False) -> ResearchProject:
        stmt = select(ResearchProject).where(ResearchProject.id == project_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one()

    async def task_ids(self, project_id: uuid.UUID) -> dict[str, uuid.UUID]:
        rows = await self.session.execute(
            select(ResearchTask.task_key, ResearchTask.id).where(
                ResearchTask.project_id == project_id
            )
        )
        return {key: task_id for key, task_id in rows.all()}

    async def upsert_tasks(
        self,
        project_id: uuid.UUID,
        tasks: dict[str, dict[str, Any]],
        outputs: dict[str, dict[str, Any]] | None = None,
        stats: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        outputs, stats = outputs or {}, stats or {}
        for key, t in tasks.items():
            result = None
            if key in outputs:
                result = {"output": outputs[key], "stats": stats.get(key, {})}
            values = {
                "project_id": project_id,
                "task_key": key,
                "agent_name": t["agent"],
                "task_description": t["description"],
                "status": TaskStatus(t["status"]),
                "priority": TaskPriority(t.get("priority", "medium")),
                "dependencies": t.get("dependencies", []),
                "attempts": t.get("attempts", 0),
                "iteration": t.get("iteration", 0),
                "error_message": t.get("error"),
                "started_at": _dt(t.get("started_at")),
                "completed_at": _dt(t.get("completed_at")),
            }
            if result is not None:
                values["result"] = result
            stmt = insert(ResearchTask).values(id=uuid.uuid4(), **values)
            update_cols = {
                k: stmt.excluded[k] for k in values if k not in ("project_id", "task_key")
            }
            await self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_research_tasks_project_task_key", set_=update_cols
                )
            )

    async def insert_sources(
        self,
        project_id: uuid.UUID,
        sources: dict[str, dict[str, Any]],
        task_ids: dict[str, uuid.UUID],
    ) -> int:
        inserted = 0
        for key, s in sources.items():
            stmt = (
                insert(ResearchSource)
                .values(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    task_id=task_ids.get(s["task_key"]),
                    citation_key=key,
                    url=s["url"],
                    normalized_url=s["normalized_url"],
                    title=s.get("title"),
                    domain=s["domain"],
                    publication_date=_dt(s.get("publication_date")),
                    retrieved_at=_dt(s["retrieved_at"]),
                    extracted_content=s.get("content"),
                    source_metadata={
                        "fetched": s.get("fetched", False),
                        "search_provider": s.get("search_provider"),
                        "query": s.get("query"),
                    },
                )
                .on_conflict_do_nothing()  # same page found again: first retrieval wins
            )
            result = await self.session.execute(stmt)
            inserted += result.rowcount or 0  # type: ignore[attr-defined]
        return inserted

    async def insert_executions(
        self,
        project_id: uuid.UUID,
        executions: list[dict[str, Any]],
        task_ids: dict[str, uuid.UUID],
    ) -> None:
        for e in executions:
            self.session.add(
                AgentExecution(
                    project_id=project_id,
                    task_id=task_ids.get(e["task_key"]) if e.get("task_key") else None,
                    agent_name=e["agent"],
                    attempt=e.get("attempt", 1),
                    status=ExecutionStatus(e["status"]),
                    model=e.get("model"),
                    input_data=e.get("input") or {},
                    output_data=e.get("output") or {},
                    prompt_tokens=e.get("prompt_tokens", 0),
                    completion_tokens=e.get("completion_tokens", 0),
                    token_usage=e.get("total_tokens", 0),
                    estimated_cost=Decimal(str(e.get("cost_usd", "0"))),
                    execution_time=e.get("duration_ms"),
                    error_message=e.get("error"),
                    started_at=_dt(e.get("started_at")),
                    finished_at=_dt(e.get("finished_at")),
                )
            )
        await self.session.flush()

    async def add_report(
        self, project_id: uuid.UUID, report: dict[str, Any], verification: dict[str, Any] | None
    ) -> int:
        current = await self.session.execute(
            select(func.coalesce(func.max(ResearchReport.version), 0)).where(
                ResearchReport.project_id == project_id
            )
        )
        version = int(current.scalar_one()) + 1
        self.session.add(
            ResearchReport(
                project_id=project_id,
                version=version,
                report_content=report["markdown"],
                structured={k: v for k, v in report.items() if k != "markdown"},
                verification=verification,
            )
        )
        await self.session.flush()
        return version

    async def set_report_quality(self, project_id: uuid.UUID, quality: dict[str, Any]) -> None:
        report = (
            await self.session.execute(
                select(ResearchReport)
                .where(ResearchReport.project_id == project_id)
                .order_by(ResearchReport.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if report is not None:
            report.structured = {**report.structured, "quality": quality}

    async def reset_for_rerun(self, project_id: uuid.UUID) -> None:
        """Tasks and sources belong to one run; executions and reports keep history."""
        await self.session.execute(
            delete(ResearchSource).where(ResearchSource.project_id == project_id)
        )
        await self.session.execute(
            delete(ResearchTask).where(ResearchTask.project_id == project_id)
        )

    async def count_active(self, user_id: uuid.UUID) -> int:
        active = [s for s in ProjectStatus if s.is_active] + [ProjectStatus.AWAITING_APPROVAL]
        result = await self.session.execute(
            select(func.count()).where(
                ResearchProject.user_id == user_id, ResearchProject.status.in_(active)
            )
        )
        return int(result.scalar_one())

    async def mark_orphans_interrupted(self) -> list[uuid.UUID]:
        """Runs that were active when the process died can't still be running."""
        active = [s for s in ProjectStatus if s.is_active]
        rows = await self.session.execute(
            select(ResearchProject).where(ResearchProject.status.in_(active)).with_for_update()
        )
        ids = []
        for p in rows.scalars():
            p.status = ProjectStatus.INTERRUPTED
            p.error_message = "Run was interrupted by a server restart; start it again to resume."
            ids.append(p.id)
        return ids

    async def agent_summary(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        e = AgentExecution
        rows = await self.session.execute(
            select(
                e.agent_name,
                func.count().label("executions"),
                func.sum(case((e.status == ExecutionStatus.SUCCEEDED, 1), else_=0)).label(
                    "succeeded"
                ),
                func.sum(case((e.status == ExecutionStatus.FAILED, 1), else_=0)).label("failed"),
                func.coalesce(func.sum(e.token_usage), 0).label("tokens"),
                func.coalesce(func.sum(e.estimated_cost), 0).label("cost"),
                func.avg(e.execution_time).label("avg_ms"),
                func.max(e.finished_at).label("last_finished_at"),
            )
            .where(e.project_id == project_id)
            .group_by(e.agent_name)
            .order_by(func.min(e.started_at))
        )
        return [dict(r._mapping) for r in rows]
