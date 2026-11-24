"""Gemini client (optional secondary provider) using JSON-schema output."""

from __future__ import annotations

import time

from google import genai
from google.genai import errors, types

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
)
from app.llm.retry import with_retries
from app.monitoring.cost import estimate_cost
from app.monitoring.tracing import trace_llm


def map_gemini_error(exc: Exception) -> LLMError:
    code = getattr(exc, "code", None)
    if code == 429:
        return LLMRateLimitError("Gemini rate limit")
    if isinstance(exc, errors.ServerError) or code in (500, 502, 503, 504):
        return LLMTransientError(f"Gemini transient error ({code})")
    if code in (401, 403):
        return LLMConfigurationError("Gemini rejected the API key")
    if code == 404:
        return LLMConfigurationError("Gemini model not found; check GEMINI_MODEL")
    return LLMError(f"Gemini error ({code}): {str(exc)[:300]}")


class GeminiClient:
    provider = "gemini"

    def __init__(self, settings: Settings, client: genai.Client | None = None) -> None:
        if not settings.provider_configured(LLMProvider.GEMINI):
            raise LLMConfigurationError("GEMINI_API_KEY is not set")
        assert settings.gemini_api_key is not None
        self._settings = settings
        self._client = client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000)),
        )

    def model_for(self, tier: ModelTier) -> str:
        return self._settings.gemini_model

    @trace_llm("google")
    async def complete_structured(self, request: StructuredRequest) -> LLMResult:
        model = self.model_for(request.tier)
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        contents = [
            types.Content(
                role="user" if m.role == "user" else "model", parts=[types.Part(text=m.content)]
            )
            for m in request.messages
            if m.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            response_mime_type="application/json",
            response_json_schema=request.json_schema,
            temperature=request.temperature,
            max_output_tokens=request.max_completion_tokens,
        )

        async def attempt() -> LLMResult:
            start = time.perf_counter()
            try:
                resp = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except errors.APIError as exc:
                raise map_gemini_error(exc) from exc
            latency = (time.perf_counter() - start) * 1000
            text = resp.text
            if not text:
                raise LLMStructuredOutputError("Gemini returned an empty response")
            meta = resp.usage_metadata
            prompt_t = (meta.prompt_token_count or 0) if meta else 0
            completion_t = (meta.candidates_token_count or 0) if meta else 0
            return LLMResult(
                content=text,
                model=model,
                usage=estimate_cost(model, prompt_t, completion_t, self._settings.llm_pricing_json),
                latency_ms=latency,
            )

        return await with_retries(attempt, max_retries=self._settings.llm_max_retries)
