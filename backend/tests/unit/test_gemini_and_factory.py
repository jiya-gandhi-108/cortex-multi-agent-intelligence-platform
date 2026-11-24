"""Gemini client (fake SDK object) and the provider factory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import errors

from app.core.config import LLMProvider
from app.llm.base import (
    ChatMessage,
    LLMConfigurationError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    ModelTier,
    StructuredRequest,
)
from app.llm.factory import get_llm_client
from app.llm.gemini_client import GeminiClient, map_gemini_error
from tests.conftest import make_settings

REQ = StructuredRequest(
    [ChatMessage("system", "be precise"), ChatMessage("user", "hi")],
    "S",
    {"type": "object"},
    ModelTier.REASONING,
    200,
)


class FakeModels:
    def __init__(self, result=None, exc=None) -> None:
        self.result, self.exc, self.calls = result, exc, []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.result


def _client(models: FakeModels) -> GeminiClient:
    cfg = make_settings(
        gemini_api_key="AIza-test",
        llm_max_retries=0,
        llm_pricing_json='{"gemini-2.5-pro": {"input": 1.25, "output": 10}}',
    )
    return GeminiClient(cfg, client=SimpleNamespace(aio=SimpleNamespace(models=models)))  # type: ignore[arg-type]


async def test_gemini_sends_schema_and_system_instruction() -> None:
    usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=100)
    models = FakeModels(SimpleNamespace(text='{"ok": true}', usage_metadata=usage))
    result = await _client(models).complete_structured(REQ)
    cfg = models.calls[0]["config"]
    assert cfg.response_mime_type == "application/json" and cfg.response_json_schema == {
        "type": "object"
    }
    assert cfg.system_instruction == "be precise"
    assert [c.role for c in models.calls[0]["contents"]] == ["user"]
    assert result.usage.prompt_tokens == 1000 and str(result.usage.cost_usd) == "0.002250"


async def test_gemini_empty_response_and_error_mapping() -> None:
    with pytest.raises(LLMStructuredOutputError):
        await _client(
            FakeModels(SimpleNamespace(text="", usage_metadata=None))
        ).complete_structured(REQ)
    assert isinstance(map_gemini_error(errors.ClientError(429, {"error": {}})), LLMRateLimitError)
    assert isinstance(
        map_gemini_error(errors.ClientError(403, {"error": {}})), LLMConfigurationError
    )
    assert map_gemini_error(errors.ServerError(503, {"error": {}})).transient


def test_factory_requires_keys_and_caches_per_settings() -> None:
    cfg = make_settings()
    with pytest.raises(LLMConfigurationError, match="GROQ_API_KEY"):
        get_llm_client(cfg)
    keyed = make_settings(groq_api_key="gsk_x", gemini_api_key="AIza-x")
    assert get_llm_client(keyed) is get_llm_client(keyed, "groq")  # shared rate governors
    assert get_llm_client(keyed, LLMProvider.GEMINI).provider == "gemini"
