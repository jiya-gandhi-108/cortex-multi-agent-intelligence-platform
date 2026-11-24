"""Use cases for running research: start / re-run / resume, approve, cancel, inspect.

State machine (project.status):

    draft ─start─▶ queued ─▶ planning ─▶ (awaiting_approval ─approve─▶) running
       ▲                                  │                              │
       │                                  └─reject─▶ cancelled           ▼
    completed / failed / cancelled ─start (re-run, new run number)    verifying ─▶ reporting
    interrupted ─start (resume from the last checkpoint, same run)        │
                                                                completed | failed
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, InvalidStateError, NotFoundError
from app.models import AgentExecution, ResearchProject, ResearchReport, ResearchSource, ResearchTask
from app.models.enums import ProjectStatus, TaskStatus
from app.repositories.projects import ProjectRepository
from app.repositories.runs import RunRepository
from app.schemas.common import Page
from app.schemas.runs import (
    AgentSummary,
    DashboardRead,
    ExecutionRead,
    ReportRead,
    ReportSummary,
    SourceRead,
    StatusRead,
    TaskCounts,
    TaskRead,
)
from app.services.runner import ResearchRunner, provider_preflight

RESTARTABLE = {
    ProjectStatus.DRAFT,
    ProjectStatus.COMPLETED,
    ProjectStatus.FAILED,
    ProjectStatus.CANCELLED,
}


class IntegrationNotConfiguredError(AppError):
    status_code = 503
    code = "integration_not_configured"
    message = "A required integration is not configured on the server"


class ExecutionService:
    def __init__(
        self, session: AsyncSession, cfg: Settings, runner: ResearchRunner | None = None
    ) -> None:
        self.session = session
        self.cfg = cfg
        self._runner = runner
        self.projects = ProjectRepository(session)
        self.runs = RunRepository(session)

    @property
    def runner(self) -> ResearchRunner:
        if self._runner is None:
            raise RuntimeError("ExecutionService constructed without a runner")
        return self._runner

    async def _owned(
        self, user_id: uuid.UUID, project_id: uuid.UUID, *, lock: bool = False
    ) -> ResearchProject:
        project = await self.projects.get_owned(project_id, user_id, for_update=lock)
        if project is None:
            raise NotFoundError("Research project not found")
        return project

    # ------------------------------------------------------------ commands

    async def start(self, user_id: uuid.UUID, project_id: uuid.UUID) -> ResearchProject:
        project = await self._owned(user_id, project_id, lock=True)
        if project.status not in RESTARTABLE | {
            ProjectStatus.INTERRUPTED
        } or self.runner.is_running(project.id):
            raise InvalidStateError(f"Project is {project.status.value}; it cannot be started now")
        missing = provider_preflight(self.cfg, (project.config or {}).get("provider"))
        if missing:
            raise IntegrationNotConfiguredError(
                f"Research cannot run until the server has: {', '.join(missing)}",
                details={"missing": missing},
            )
        if await self.runs.count_active(user_id) >= self.cfg.max_active_runs_per_user:
            raise ConflictError(
                f"You already have {self.cfg.max_active_runs_per_user} active runs; "
                "wait for one to finish or cancel it"
            )

        if project.status is ProjectStatus.INTERRUPTED:
            payload: Any = None  # LangGraph resumes the thread from its last checkpoint
        else:
            await self.runs.reset_for_rerun(project.id)
            project.run_count += 1
            project.plan = None
            project.total_tokens = 0
            project.estimated_cost = 0  # type: ignore[assignment]
            project.run_summary = None
            project.started_at = datetime.now(UTC)
            payload = self.runner.fresh_input(project)
        project.status = ProjectStatus.QUEUED
        project.current_stage = "queued"
        project.error_message = None
        project.completed_at = None
        await self.session.commit()  # the background run reads committed state
        self.runner.launch(project.id, project.run_count, payload)
        return project

    async def approve(
        self, user_id: uuid.UUID, project_id: uuid.UUID, approved: bool, reason: str | None
    ) -> ResearchProject:
        project = await self._owned(user_id, project_id, lock=True)
        if project.status is not ProjectStatus.AWAITING_APPROVAL or self.runner.is_running(
            project.id
        ):
            raise InvalidStateError("Project is not waiting for plan approval")
        project.status = ProjectStatus.RUNNING if approved else ProjectStatus.CANCELLED
        project.current_stage = "approved" if approved else "plan_rejected"
        await self.session.commit()
        self.runner.launch(
            project.id, project.run_count, self.runner.approval_input(approved, reason)
        )
        return project

    async def cancel(self, user_id: uuid.UUID, project_id: uuid.UUID) -> ResearchProject:
        project = await self._owned(user_id, project_id, lock=True)
        if self.runner.is_running(project.id):
            await self.runner.cancel(project.id)
            project.current_stage = "cancelling"
            return project
        if project.status in (
            ProjectStatus.AWAITING_APPROVAL,
            ProjectStatus.QUEUED,
            ProjectStatus.INTERRUPTED,
        ):
            project.status = ProjectStatus.CANCELLED
            project.current_stage = "cancelled"
            project.error_message = "Cancelled by user"
            project.completed_at = datetime.now(UTC)
            return project
        raise InvalidStateError(f"Project is {project.status.value}; nothing to cancel")

    # ------------------------------------------------------------- queries

    async def status(self, user_id: uuid.UUID, project_id: uuid.UUID) -> StatusRead:
        project = await self._owned(user_id, project_id)
        tasks = (
            (
                await self.session.execute(
                    select(ResearchTask)
                    .where(ResearchTask.project_id == project.id)
                    .order_by(ResearchTask.task_key)
                )
            )
            .scalars()
            .all()
        )
        counts = TaskCounts(total=len(tasks))
        for t in tasks:
            if t.status in (
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.SKIPPED,
            ):
                setattr(counts, t.status.value, getattr(counts, t.status.value) + 1)
        sources = (
            await self.session.execute(
                select(func.count()).where(ResearchSource.project_id == project.id)
            )
        ).scalar_one()
        end = project.completed_at or (datetime.now(UTC) if project.status.is_active else None)
        duration = (
            (end - project.started_at).total_seconds() if end and project.started_at else None
        )
        summary = dict(project.run_summary or {})
        summary.pop("verification_full", None)
        return StatusRead(
            project_id=project.id,
            status=project.status,
            stage=project.current_stage,
            run=project.run_count,
            error_message=project.error_message,
            started_at=project.started_at,
            completed_at=project.completed_at,
            duration_seconds=duration,
            total_tokens=project.total_tokens,
            estimated_cost=project.estimated_cost,
            sources=int(sources),
            tasks=counts,
            task_list=[TaskRead.model_validate(t) for t in tasks],
            plan=project.plan,
            awaiting_approval=project.status is ProjectStatus.AWAITING_APPROVAL,
            summary=summary or None,
            events_url=f"{self.cfg.api_v1_prefix}/research/{project.id}/events",
        )

    async def tasks(self, user_id: uuid.UUID, project_id: uuid.UUID) -> list[TaskRead]:
        project = await self._owned(user_id, project_id)
        rows = await self.session.execute(
            select(ResearchTask)
            .where(ResearchTask.project_id == project.id)
            .order_by(ResearchTask.task_key)
        )
        return [TaskRead.model_validate(t) for t in rows.scalars()]

    async def agents(self, user_id: uuid.UUID, project_id: uuid.UUID) -> list[AgentSummary]:
        project = await self._owned(user_id, project_id)
        return [AgentSummary.model_validate(r) for r in await self.runs.agent_summary(project.id)]

    async def executions(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        agent: str | None = None,
    ) -> Page[ExecutionRead]:
        project = await self._owned(user_id, project_id)
        base = select(AgentExecution).where(AgentExecution.project_id == project.id)
        if agent:
            base = base.where(AgentExecution.agent_name == agent)
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = await self.session.execute(
            base.order_by(AgentExecution.started_at.desc(), AgentExecution.id)
            .limit(limit)
            .offset(offset)
        )
        return Page[ExecutionRead](
            items=[ExecutionRead.model_validate(e) for e in rows.scalars()],
            total=int(total),
            limit=limit,
            offset=offset,
        )

    async def sources(
        self, user_id: uuid.UUID, project_id: uuid.UUID, *, limit: int, offset: int
    ) -> Page[SourceRead]:
        project = await self._owned(user_id, project_id)
        base = select(ResearchSource).where(ResearchSource.project_id == project.id)
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = await self.session.execute(
            base.outerjoin(ResearchTask, ResearchTask.id == ResearchSource.task_id)
            .add_columns(ResearchTask.task_key)
            .order_by(ResearchSource.retrieved_at, ResearchSource.citation_key)
            .limit(limit)
            .offset(offset)
        )
        items = []
        for src, task_key in rows.all():
            item = SourceRead.model_validate(src)
            item.task_key = task_key
            item.fetched = bool((src.source_metadata or {}).get("fetched"))
            item.excerpt = (src.extracted_content or "")[:600] or None
            items.append(item)
        return Page[SourceRead](items=items, total=int(total), limit=limit, offset=offset)

    async def report(
        self, user_id: uuid.UUID, project_id: uuid.UUID, version: int | None = None
    ) -> tuple[ResearchProject, ReportRead]:
        project = await self._owned(user_id, project_id)
        stmt = select(ResearchReport).where(ResearchReport.project_id == project.id)
        stmt = (
            stmt.where(ResearchReport.version == version)
            if version
            else stmt.order_by(ResearchReport.version.desc()).limit(1)
        )
        report = (await self.session.execute(stmt)).scalar_one_or_none()
        if report is None:
            raise NotFoundError("No report available for this project yet")
        return project, ReportRead.model_validate(report)

    async def dashboard(self, user_id: uuid.UUID) -> DashboardRead:
        p, t, r = ResearchProject, ResearchTask, ResearchReport
        rows = await self.session.execute(
            select(p.status, func.count()).where(p.user_id == user_id).group_by(p.status)
        )
        by_status = {status: count for status, count in rows.all()}
        totals = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(p.total_tokens), 0),
                    func.coalesce(func.sum(p.estimated_cost), 0),
                ).where(p.user_id == user_id)
            )
        ).one()
        completed_tasks = (
            await self.session.execute(
                select(func.count())
                .select_from(t)
                .join(p, p.id == t.project_id)
                .where(p.user_id == user_id, t.status == TaskStatus.COMPLETED)
            )
        ).scalar_one()
        report_count = (
            await self.session.execute(
                select(func.count())
                .select_from(r)
                .join(p, p.id == r.project_id)
                .where(p.user_id == user_id)
            )
        ).scalar_one()
        recent = (
            (
                await self.session.execute(
                    select(p).where(p.user_id == user_id).order_by(p.updated_at.desc()).limit(5)
                )
            )
            .scalars()
            .all()
        )
        reports = (
            await self.session.execute(
                select(r, p.title)
                .join(p, p.id == r.project_id)
                .where(p.user_id == user_id)
                .order_by(r.created_at.desc())
                .limit(5)
            )
        ).all()
        active = {s for s in ProjectStatus if s.is_active}
        return DashboardRead(
            total_projects=sum(by_status.values()),
            projects_by_status={s.value: n for s, n in by_status.items()},
            completed_tasks=int(completed_tasks),
            active_executions=sum(n for s, n in by_status.items() if s in active),
            total_reports=int(report_count),
            total_tokens=int(totals[0]),
            estimated_cost=totals[1],
            recent_projects=[
                {
                    "id": str(x.id),
                    "title": x.title,
                    "status": x.status.value,
                    "updated_at": x.updated_at.isoformat(),
                    "run": x.run_count,
                }
                for x in recent
            ],
            recent_reports=[
                ReportSummary(
                    project_id=rep.project_id,
                    project_title=title,
                    version=rep.version,
                    created_at=rep.created_at,
                    evidence_score=(rep.verification or {}).get("evidence_score"),
                )
                for rep, title in reports
            ],
        )
