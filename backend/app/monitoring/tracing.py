"""LangSmith tracing.

LangGraph traces the workflow (nodes, transitions) automatically once the
LangSmith environment variables are set. Cortex calls provider SDKs directly,
so LLM calls and tool calls are wrapped with ``traceable`` here to appear in
the same trace, with token usage in LangSmith's ``usage_metadata`` format.

When tracing is off, ``traceable`` is a cheap pass-through.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

from langsmith import traceable

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def configure_tracing(settings: Settings) -> bool:
    """Export LangSmith settings to the environment (the SDK reads os.environ)."""
    enabled = bool(settings.integration_status()["langsmith"])
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if enabled:
        assert settings.langsmith_api_key is not None
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    log.info(
        "tracing_configured",
        langsmith=enabled,
        project=settings.langsmith_project if enabled else None,
    )
    return enabled


def _llm_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    if request is None:
        return inputs
    return {
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        "schema": request.schema_name,
        "tier": request.tier.value,
        "max_completion_tokens": request.max_completion_tokens,
    }


def _llm_outputs(output: Any) -> dict[str, Any]:
    usage = getattr(output, "usage", None)
    if usage is None:
        return {"output": output}
    return {
        "content": output.content,
        "model": output.model,
        "latency_ms": round(output.latency_ms, 1),
        "usage_metadata": {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
        "estimated_cost_usd": str(usage.cost_usd),
    }


def trace_llm(provider: str) -> Callable[[F], F]:
    return traceable(  # type: ignore[return-value]
        run_type="llm",
        name=f"{provider}.complete_structured",
        metadata={"ls_provider": provider},
        process_inputs=_llm_inputs,
        process_outputs=_llm_outputs,
    )


def trace_tool(name: str) -> Callable[[F], F]:
    return traceable(run_type="tool", name=name)  # type: ignore[return-value]
