"""Run control, live events, execution data, sources and reports for a project."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import Response
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.deps import CurrentUser, SessionDep, SettingsDep, user_rate_limit
from app.core.errors import ServiceUnavailableError
from app.core.rate_limit import enforce_rate_limit
from app.models.enums import ProjectStatus
from app.schemas.common import ERROR_RESPONSES, ErrorResponse, Page, PageParams
from app.schemas.runs import (
    AgentSummary,
    ApprovalRequest,
    ExecutionRead,
    ReportRead,
    SourceRead,
    StatusRead,
    TaskRead,
)
from app.services.events import is_terminal, read_events, stream_key
from app.services.execution import ExecutionService
from app.services.report_export import render_pdf
from app.services.runner import ResearchRunner

router = APIRouter(
    prefix="/research",
    tags=["research-runs"],
    dependencies=[Depends(user_rate_limit)],
    responses={401: ERROR_RESPONSES[401], 404: ERROR_RESPONSES[404], 429: ERROR_RESPONSES[429]},
)
STATE_ERRORS: dict[int | str, dict[str, object]] = {
    409: {"model": ErrorResponse, "description": "Not allowed in the project's current state"},
    503: {"model": ErrorResponse, "description": "LLM or search provider not configured"},
}


def get_runner(request: Request) -> ResearchRunner:
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise ServiceUnavailableError("Research execution is not available")
    return runner  # type: ignore[no-any-return]


RunnerDep = Annotated[ResearchRunner, Depends(get_runner)]


def _service(session: SessionDep, cfg: SettingsDep, runner: RunnerDep) -> ExecutionService:
    return ExecutionService(session, cfg, runner)


ServiceDep = Annotated[ExecutionService, Depends(_service)]


async def start_rate_limit(request: Request, user: CurrentUser, cfg: SettingsDep) -> None:
    await enforce_rate_limit(
        request, f"research-start:{user.id}", cfg.research_start_limit_per_hour, window_seconds=3600
    )


# ------------------------------------------------------------------ control


@router.post(
    "/{project_id}/start",
    response_model=StatusRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(start_rate_limit)],
    responses=STATE_ERRORS,
    summary="Start, re-run (after completion/failure) or resume (after interruption)",
)
async def start(project_id: uuid.UUID, user: CurrentUser, svc: ServiceDep) -> StatusRead:
    await svc.start(user.id, project_id)
    return await svc.status(user.id, project_id)


@router.post(
    "/{project_id}/approve", response_model=StatusRead, status_code=202, responses=STATE_ERRORS
)
async def approve(
    project_id: uuid.UUID, body: ApprovalRequest, user: CurrentUser, svc: ServiceDep
) -> StatusRead:
    """Approve or reject the generated plan (projects with plan approval enabled)."""
    await svc.approve(user.id, project_id, body.approved, body.reason)
    return await svc.status(user.id, project_id)


@router.post(
    "/{project_id}/cancel", response_model=StatusRead, status_code=202, responses=STATE_ERRORS
)
async def cancel(project_id: uuid.UUID, user: CurrentUser, svc: ServiceDep) -> StatusRead:
    await svc.cancel(user.id, project_id)
    return await svc.status(user.id, project_id)


# ------------------------------------------------------------------- status


@router.get("/{project_id}/status", response_model=StatusRead)
async def get_status(project_id: uuid.UUID, user: CurrentUser, svc: ServiceDep) -> StatusRead:
    return await svc.status(user.id, project_id)


@router.get("/{project_id}/tasks", response_model=list[TaskRead])
async def list_tasks(project_id: uuid.UUID, user: CurrentUser, svc: ServiceDep) -> list[TaskRead]:
    return await svc.tasks(user.id, project_id)


@router.get("/{project_id}/agents", response_model=list[AgentSummary])
async def list_agents(
    project_id: uuid.UUID, user: CurrentUser, svc: ServiceDep
) -> list[AgentSummary]:
    """Per-agent aggregates: executions, outcomes, tokens, cost, latency."""
    return await svc.agents(user.id, project_id)


@router.get("/{project_id}/executions", response_model=Page[ExecutionRead])
async def list_executions(
    project_id: uuid.UUID,
    user: CurrentUser,
    svc: ServiceDep,
    page: Annotated[PageParams, Depends()],
    agent: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[ExecutionRead]:
    return await svc.executions(
        user.id, project_id, limit=page.limit, offset=page.offset, agent=agent
    )


@router.get("/{project_id}/sources", response_model=Page[SourceRead])
async def list_sources(
    project_id: uuid.UUID,
    user: CurrentUser,
    svc: ServiceDep,
    page: Annotated[PageParams, Depends()],
) -> Page[SourceRead]:
    return await svc.sources(user.id, project_id, limit=page.limit, offset=page.offset)


# ------------------------------------------------------------------- events


@router.get(
    "/{project_id}/events",
    summary="Live execution events (Server-Sent Events)",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(
    project_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    svc: ServiceDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=64)] = None,
) -> EventSourceResponse:
    """Streams workflow events for the project's current run.

    Starts with a ``snapshot`` event (current status), then replays the run's events
    (or resumes after ``Last-Event-ID``) and follows live until the run finishes or
    pauses. Authenticate with the ``Authorization`` header (use a fetch-based SSE client;
    tokens are deliberately not accepted in the URL, where they would be logged).
    """
    snapshot = await svc.status(user.id, project_id)  # ownership check + initial state
    redis = request.app.state.redis
    key = stream_key(str(project_id), snapshot.run)
    cursor = last_event_id if last_event_id and re.fullmatch(r"\d+-\d+", last_event_id) else "0"
    live = snapshot.status.is_active
    has_run = snapshot.run > 0

    async def stream() -> AsyncIterator[ServerSentEvent]:
        yield ServerSentEvent(
            event="snapshot",
            data=snapshot.model_dump_json(
                include={
                    "project_id",
                    "status",
                    "stage",
                    "run",
                    "tasks",
                    "sources",
                    "total_tokens",
                    "error_message",
                    "awaiting_approval",
                }
            ),
        )
        if not has_run or redis is None:
            return
        if not live:  # finished/paused run: replay what happened, then close
            entries = await redis.xrange(key, min="(" + cursor if cursor != "0" else "-", max="+")
            for entry_id, fields in entries:
                raw = fields.get("data") or fields.get(b"data")
                yield ServerSentEvent(id=str(entry_id), event=json.loads(raw)["type"], data=raw)
            return
        async for entry_id, event in read_events(redis, key, cursor):
            if await request.is_disconnected():
                return
            if event is None:
                continue  # idle; sse-starlette sends keep-alive pings
            yield ServerSentEvent(
                id=entry_id, event=event.get("type", "message"), data=json.dumps(event)
            )
            if is_terminal(event):
                return

    return EventSourceResponse(stream(), ping=15, headers={"X-Accel-Buffering": "no"})


# ------------------------------------------------------------------- report


def _filename(title: str, version: int, ext: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"
    return f"cortex-{slug}-v{version}.{ext}"


def _disposition(title: str, version: int, ext: str) -> str:
    return f'attachment; filename="{_filename(title, version, ext)}"'


@router.get("/{project_id}/report", response_model=ReportRead)
async def get_report(
    project_id: uuid.UUID,
    user: CurrentUser,
    svc: ServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ReportRead:
    _, report = await svc.report(user.id, project_id, version)
    return report


@router.get(
    "/{project_id}/report/download",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
)
async def download_report(
    project_id: uuid.UUID,
    user: CurrentUser,
    svc: ServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    project, report = await svc.report(user.id, project_id, version)
    return Response(
        report.report_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _disposition(project.title, report.version, "md")},
    )


@router.get(
    "/{project_id}/report/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def report_pdf(
    project_id: uuid.UUID,
    user: CurrentUser,
    svc: ServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    project, report = await svc.report(user.id, project_id, version)
    pdf = await render_pdf(report.report_content, project.title)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": _disposition(project.title, report.version, "pdf")},
    )


__all__ = ["ProjectStatus", "router"]
