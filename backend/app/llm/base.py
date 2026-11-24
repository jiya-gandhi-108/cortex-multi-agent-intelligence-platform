"""Provider-neutral LLM interface used by every agent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.monitoring.cost import TokenUsage


class ModelTier(StrEnum):
    REASONING = "reasoning"  # planning, analysis, verification, report writing
    FAST = "fast"  # query generation, evidence extraction


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class StructuredRequest:
    messages: list[ChatMessage]
    schema_name: str
    json_schema: dict[str, Any]
    tier: ModelTier = ModelTier.REASONING
    max_completion_tokens: int = 2_000
    temperature: float = 0.2


@dataclass(frozen=True)
class LLMResult:
    content: str  # raw JSON text, validated by the caller
    model: str
    usage: TokenUsage
    latency_ms: float


class LLMError(Exception):
    """Base for provider failures. ``transient`` errors are worth retrying."""

    transient: bool = False


class LLMTransientError(LLMError):
    transient = True

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMRateLimitError(LLMTransientError):
    pass


class LLMStructuredOutputError(LLMError):
    """Model failed to produce schema-conforming JSON (provider-side or ours)."""


class LLMConfigurationError(LLMError):
    """Missing key, unknown model, auth failure. Never retried."""


class LLMClient(Protocol):
    provider: str

    def model_for(self, tier: ModelTier) -> str: ...

    async def complete_structured(self, request: StructuredRequest) -> LLMResult: ...


def estimate_tokens(text: str) -> int:
    """Cheap upper-ish estimate (~4 chars/token for English) for budgeting/throttling."""
    return max(1, len(text) // 4 + 1)
