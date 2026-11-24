"""Shared agent runtime: context, structured LLM calls, validation and repair.

``structured_call`` is the only way agents talk to an LLM. It:
  1. refuses prompts above ``llm_max_prompt_tokens`` (builders must trim),
  2. checks the project budget before spending anything,
  3. requests strict JSON-schema output,
  4. validates with Pydantic and an optional semantic validator,
  5. on failure, makes exactly one repair attempt with the errors fed back,
  6. records usage on the shared budget and emits an ``llm_call`` event.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.graph.events import EventSink, EventType, NullSink, WorkflowEvent
from app.llm.base import (
    ChatMessage,
    LLMClient,
    LLMStructuredOutputError,
    ModelTier,
    StructuredRequest,
    estimate_tokens,
)
from app.llm.schema import to_strict_schema
from app.monitoring.cost import BudgetTracker, TokenUsage
from app.schemas.evidence import SourceRecord
from app.tools.evidence import EvidenceCollector
from app.tools.sanitize import UNTRUSTED_CONTENT_RULE, source_block

_log = get_logger("app.agents")


class WorkflowCancelledError(Exception):
    pass


class AgentOutputError(Exception):
    """The model's output stayed invalid after the repair attempt."""


class PromptTooLargeError(Exception):
    """A prompt builder exceeded the configured ceiling (a bug, not a runtime condition)."""


@dataclass
class RunContext:
    """Per-run dependencies, injected via LangGraph's runtime context (not checkpointed)."""

    project_id: str
    settings: Settings
    llm: LLMClient
    collector: EvidenceCollector
    budget: BudgetTracker
    sink: EventSink = field(default_factory=NullSink)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise WorkflowCancelledError("workflow cancelled")

    async def emit(self, type_: EventType, **kw: Any) -> None:
        await self.sink.emit(WorkflowEvent(type=type_, project_id=self.project_id, **kw))


@dataclass
class CallLog:
    """Usage accumulated by one agent execution (for the execution record)."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None
    llm_calls: int = 0
    repairs: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "total_tokens": self.usage.total_tokens,
            "cost_usd": str(self.usage.cost_usd),
            "priced": self.usage.priced,
            "model": self.model,
            "llm_calls": self.llm_calls,
            "repairs": self.repairs,
        }


def system_prompt(role: str, *, untrusted: bool = True) -> str:
    rules = [
        role.strip(),
        "Ground every factual statement in the provided evidence and cite source ids "
        "exactly as given (e.g. S3f9a2c). Never invent sources, URLs, numbers, prices or "
        "company facts. If the evidence does not contain something, say it is unknown.",
        "Respond only with JSON matching the required schema.",
    ]
    if untrusted:
        rules.insert(1, UNTRUSTED_CONTENT_RULE)
    return "\n\n".join(rules)


def render_sources(sources: Sequence[SourceRecord], excerpt_chars: int, budget_chars: int) -> str:
    """Render source blocks until the character budget is spent (most useful first)."""
    blocks: list[str] = []
    used = 0
    for s in sources:
        published = s.publication_date.date().isoformat() if s.publication_date else None
        block = source_block(s.citation_key, s.title, s.domain, published, s.content, excerpt_chars)
        if used + len(block) > budget_chars and blocks:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def prompt_char_budget(settings: Settings, reserved_chars: int) -> int:
    """Characters available for evidence after fixed prompt text (~4 chars/token)."""
    return max(1_000, settings.llm_max_prompt_tokens * 4 - reserved_chars)


async def structured_call[M: BaseModel](
    ctx: RunContext,
    log: CallLog,
    *,
    agent: str,
    output_model: type[M],
    system: str,
    user: str,
    tier: ModelTier,
    max_completion_tokens: int,
    validate: Callable[[M], list[str]] | None = None,
    task_key: str | None = None,
) -> M:
    ctx.check_cancelled()
    prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
    if prompt_tokens > ctx.settings.llm_max_prompt_tokens * 1.15:
        raise PromptTooLargeError(
            f"{agent}: prompt ~{prompt_tokens} tokens exceeds {ctx.settings.llm_max_prompt_tokens}"
        )

    messages = [ChatMessage("system", system), ChatMessage("user", user)]
    schema = to_strict_schema(output_model)
    last_error = "unknown"

    for attempt in range(2):  # initial + one repair
        ctx.budget.check(reserve_tokens=prompt_tokens + max_completion_tokens)
        request = StructuredRequest(
            messages=messages,
            schema_name=output_model.__name__,
            json_schema=schema,
            tier=tier,
            max_completion_tokens=max_completion_tokens,
        )
        try:
            result = await ctx.llm.complete_structured(request)
        except LLMStructuredOutputError as exc:
            last_error = str(exc)
            log.repairs += attempt == 0
            continue
        ctx.budget.record(result.usage)
        _log.info(
            "llm_call",
            project_id=ctx.project_id,
            agent=agent,
            task=task_key,
            model=result.model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cost_usd=str(result.usage.cost_usd),
            latency_ms=round(result.latency_ms, 1),
            repair=attempt > 0,
        )
        log.usage = log.usage + result.usage
        log.model = result.model
        log.llm_calls += 1
        await ctx.emit(
            EventType.LLM_CALL,
            agent=agent,
            task_key=task_key,
            data={
                "model": result.model,
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "latency_ms": round(result.latency_ms, 1),
                "repair": attempt > 0,
            },
        )

        errors: list[str]
        parsed: M | None = None
        try:
            parsed = output_model.model_validate(json.loads(result.content))
            errors = validate(parsed) if validate else []
        except (json.JSONDecodeError, ValidationError) as exc:
            errors = _format_errors(exc)
        if not errors and parsed is not None:
            return parsed

        last_error = "; ".join(errors)[:1500]
        if attempt == 0:
            log.repairs += 1
            messages = [
                *messages,
                ChatMessage("assistant", result.content[:4000]),
                ChatMessage(
                    "user",
                    "Your previous response was invalid:\n- "
                    + "\n- ".join(errors[:12])
                    + "\nReturn a corrected JSON object that fixes every issue.",
                ),
            ]
    raise AgentOutputError(f"{agent}: invalid output after repair: {last_error}")


def _format_errors(exc: Exception) -> list[str]:
    if isinstance(exc, ValidationError):
        return [
            f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}" for e in exc.errors()[:12]
        ]
    return [f"response was not valid JSON: {exc}"]


def unknown_ids(ids: Sequence[str], valid: set[str]) -> list[str]:
    return sorted({i for i in ids if i not in valid})


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def decimal_str(value: Decimal) -> str:
    return format(value, "f")
