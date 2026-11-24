"""Groq chat-completions client with strict JSON-schema structured output."""

from __future__ import annotations

import time

import groq

from app.core.config import LLMProvider, Settings
from app.llm.base import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMResult,
    LLMStructuredOutputError,
    LLMTransientError,
    ModelTier,
    StructuredRequest,
    estimate_tokens,
)
from app.llm.retry import with_retries
from app.llm.throttle import RateGovernor
from app.monitoring.cost import estimate_cost
from app.monitoring.tracing import trace_llm

_REASONING_EFFORT = {ModelTier.REASONING: "medium", ModelTier.FAST: "low"}


def _retry_after(exc: groq.APIStatusError) -> float | None:
    raw = exc.response.headers.get("retry-after") if exc.response is not None else None
    try:
        return min(float(raw), 120.0) if raw is not None else None
    except ValueError:
        return None


def map_groq_error(exc: Exception) -> LLMError:
    if isinstance(exc, groq.RateLimitError):
        return LLMRateLimitError("Groq rate limit", retry_after=_retry_after(exc))
    if isinstance(exc, (groq.APITimeoutError, groq.APIConnectionError, groq.InternalServerError)):
        return LLMTransientError(f"Groq transient error: {type(exc).__name__}")
    if isinstance(exc, groq.APIStatusError) and exc.status_code in (502, 503, 504):
        return LLMTransientError(f"Groq HTTP {exc.status_code}")
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
        return LLMConfigurationError("Groq rejected the API key")
    if isinstance(exc, groq.NotFoundError):
        return LLMConfigurationError("Groq model not found; check GROQ_MODEL_* settings")
    if isinstance(exc, groq.BadRequestError):
        body = str(getattr(exc, "body", "") or exc)
        if "json_validate_failed" in body or "schema" in body.lower():
            return LLMStructuredOutputError("Groq could not produce schema-valid JSON")
        return LLMError(f"Groq rejected the request: {body[:300]}")
    return LLMError(f"Groq error: {type(exc).__name__}")


class GroqClient:
    provider = "groq"

    def __init__(self, settings: Settings, client: groq.AsyncGroq | None = None) -> None:
        if not settings.provider_configured(LLMProvider.GROQ):
            raise LLMConfigurationError("GROQ_API_KEY is not set")
        assert settings.groq_api_key is not None
        self._settings = settings
        self._client = client or groq.AsyncGroq(
            api_key=settings.groq_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # retries are ours: backoff + governor awareness
        )
        # Groq limits are per model, so each model gets its own window.
        self._governors = {
            model: RateGovernor(settings.groq_tokens_per_minute, settings.groq_requests_per_minute)
            for model in {settings.groq_model_reasoning, settings.groq_model_fast}
        }

    def model_for(self, tier: ModelTier) -> str:
        s = self._settings
        return s.groq_model_reasoning if tier is ModelTier.REASONING else s.groq_model_fast

    @trace_llm("groq")
    async def complete_structured(self, request: StructuredRequest) -> LLMResult:
        model = self.model_for(request.tier)
        governor = self._governors[model]
        prompt_estimate = sum(estimate_tokens(m.content) for m in request.messages)
        kwargs: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_completion_tokens": request.max_completion_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": True,
                },
            },
        }
        if model.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = _REASONING_EFFORT[request.tier]
            kwargs["include_reasoning"] = False

        async def attempt() -> LLMResult:
            reservation = await governor.acquire(prompt_estimate + request.max_completion_tokens)
            start = time.perf_counter()
            try:
                resp = await self._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]
            except groq.GroqError as exc:
                governor.settle(reservation, prompt_estimate)
                mapped = map_groq_error(exc)
                if isinstance(mapped, LLMRateLimitError):
                    governor.penalise(mapped.retry_after or 5.0)
                raise mapped from exc
            latency = (time.perf_counter() - start) * 1000
            usage = resp.usage
            prompt_t = usage.prompt_tokens if usage else prompt_estimate
            completion_t = usage.completion_tokens if usage else 0
            governor.settle(reservation, prompt_t + completion_t)
            choice = resp.choices[0] if resp.choices else None
            content = choice.message.content if choice and choice.message else None
            if not content:
                raise LLMStructuredOutputError("Groq returned an empty completion")
            if choice is not None and choice.finish_reason == "length":
                raise LLMStructuredOutputError("completion truncated at max_completion_tokens")
            return LLMResult(
                content=content,
                model=model,
                usage=estimate_cost(model, prompt_t, completion_t, self._settings.llm_pricing_json),
                latency_ms=latency,
            )

        return await with_retries(attempt, max_retries=self._settings.llm_max_retries)
