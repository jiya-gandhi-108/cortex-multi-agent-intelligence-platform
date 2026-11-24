"""LLM layer: strict schemas, throttling, retries, Groq client, cost (network mocked)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from app.llm.base import (
    ChatMessage,
    LLMConfigurationError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    LLMTransientError,
    ModelTier,
    StructuredRequest,
)
from app.llm.retry import with_retries
from app.llm.schema import SchemaNotStrictError, to_strict_schema
from app.llm.throttle import RateGovernor
from app.monitoring.cost import BudgetExceededError, BudgetTracker, TokenUsage, estimate_cost
from tests.conftest import make_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ----------------------------------------------------------------- schema


class Inner(BaseModel):
    name: str = Field(max_length=10)
    note: str | None


class Outer(BaseModel):
    items: list[Inner] = Field(min_length=1)
    score: float = Field(ge=0, le=1)


def test_strict_schema_requires_all_and_forbids_extra() -> None:
    schema = to_strict_schema(Outer)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items", "score"]
    inner = schema["$defs"]["Inner"]
    assert inner["additionalProperties"] is False and inner["required"] == ["name", "note"]
    dumped = json.dumps(schema)
    for kw in ("maxLength", "minItems", "minimum", "maximum"):
        assert kw not in dumped


def test_strict_schema_rejects_defaults() -> None:
    class Loose(BaseModel):
        a: str
        b: int = 3

    with pytest.raises(SchemaNotStrictError, match="b"):
        to_strict_schema(Loose)


# --------------------------------------------------------------- throttle


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_governor_admits_within_limits_and_waits_beyond() -> None:
    clock = FakeClock()
    g = RateGovernor(tokens_per_minute=1000, requests_per_minute=3, clock=clock)
    assert g.wait_time(600, clock.t) == 0
    g._entries.append(type(g._entries)([])[0] if False else _res(clock.t, 600))
    clock.t += 10
    assert g.wait_time(300, clock.t) == 0  # 900 <= 1000
    g._entries.append(_res(clock.t, 300))
    clock.t += 5
    wait = g.wait_time(500, clock.t)  # needs the first (600) to expire at t0+60
    assert wait == pytest.approx(45.0)


def test_governor_request_count_limit_and_penalty() -> None:
    clock = FakeClock()
    g = RateGovernor(tokens_per_minute=10_000, requests_per_minute=2, clock=clock)
    g._entries.extend([_res(clock.t, 1), _res(clock.t + 1, 1)])
    clock.t += 2
    assert g.wait_time(1, clock.t) == pytest.approx(58.0)
    g2 = RateGovernor(tokens_per_minute=10_000, requests_per_minute=10, clock=clock)
    g2.penalise(7)
    assert g2.wait_time(1, clock.t) == pytest.approx(7.0)


def test_governor_admits_oversized_request_into_empty_window() -> None:
    clock = FakeClock()
    g = RateGovernor(tokens_per_minute=100, requests_per_minute=5, clock=clock)
    assert g.wait_time(5_000, clock.t) == 0


def _res(at: float, tokens: int):
    from app.llm.throttle import Reservation

    return Reservation(at, tokens)


# ------------------------------------------------------------------ retry


async def test_retry_honours_retry_after_then_succeeds() -> None:
    calls, sleeps = 0, []

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMRateLimitError("slow down", retry_after=2.5)
        return "ok"

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    assert await with_retries(flaky, max_retries=3, sleep=fake_sleep) == "ok"
    assert sleeps == [2.5, 2.5]


async def test_retry_gives_up_and_never_retries_permanent_errors() -> None:
    async def always_transient() -> None:
        raise LLMTransientError("down")

    async def permanent() -> None:
        raise LLMConfigurationError("bad key")

    async def no_sleep(_: float) -> None:
        return None

    with pytest.raises(LLMTransientError):
        await with_retries(always_transient, max_retries=2, sleep=no_sleep)
    calls = 0

    async def counted() -> None:
        nonlocal calls
        calls += 1
        await permanent()

    with pytest.raises(LLMConfigurationError):
        await with_retries(counted, max_retries=5, sleep=no_sleep)
    assert calls == 1


# ------------------------------------------------------------ groq client


def _completion(
    content: str, prompt: int = 120, completion: int = 40, finish: str = "stop"
) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def _request(tier: ModelTier = ModelTier.REASONING) -> StructuredRequest:
    return StructuredRequest(
        messages=[ChatMessage("system", "s"), ChatMessage("user", "u")],
        schema_name="Outer",
        json_schema=to_strict_schema(Outer),
        tier=tier,
        max_completion_tokens=500,
    )


def _client(**overrides):
    from app.llm.groq_client import GroqClient

    cfg = make_settings(
        groq_api_key="gsk_test",
        llm_max_retries=2,
        llm_pricing_json='{"openai/gpt-oss-120b": {"input": 0.15, "output": 0.60}}',
        **overrides,
    )
    return GroqClient(cfg)


@respx.mock
async def test_groq_success_sends_strict_schema_and_costs_usage() -> None:
    route = respx.post(GROQ_URL).mock(
        return_value=httpx.Response(200, json=_completion('{"items": [], "score": 0.5}'))
    )
    result = await _client().complete_structured(_request())
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "medium" and body["include_reasoning"] is False
    assert result.usage.prompt_tokens == 120 and result.usage.completion_tokens == 40
    assert result.usage.cost_usd == Decimal("0.000042")  # (120*0.15 + 40*0.60)/1e6
    assert result.usage.priced


@respx.mock
async def test_groq_fast_tier_uses_small_model_and_unpriced_flag() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=_completion("{}")))
    result = await _client().complete_structured(_request(ModelTier.FAST))
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "openai/gpt-oss-20b" and body["reasoning_effort"] == "low"
    assert result.usage.priced is False  # no price configured for 20b


@respx.mock
async def test_groq_429_retried_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("app.llm.retry.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.llm.throttle.asyncio.sleep", fake_sleep)
    route = respx.post(GROQ_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "rate"}}),
            httpx.Response(200, json=_completion("{}")),
        ]
    )
    await _client().complete_structured(_request())
    assert route.call_count == 2
    assert 3.0 in sleeps


@respx.mock
async def test_groq_schema_failure_and_auth_errors_not_retried() -> None:
    route = respx.post(GROQ_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Failed to validate JSON", "code": "json_validate_failed"}},
        )
    )
    with pytest.raises(LLMStructuredOutputError):
        await _client().complete_structured(_request())
    assert route.call_count == 1

    respx.post(GROQ_URL).mock(return_value=httpx.Response(401, json={"error": {"message": "bad"}}))
    with pytest.raises(LLMConfigurationError):
        await _client().complete_structured(_request())


@respx.mock
async def test_groq_truncated_completion_is_an_error() -> None:
    respx.post(GROQ_URL).mock(
        return_value=httpx.Response(200, json=_completion('{"items": [', finish="length"))
    )
    with pytest.raises(LLMStructuredOutputError, match="truncated"):
        await _client().complete_structured(_request())


def test_groq_client_requires_key() -> None:
    from app.llm.groq_client import GroqClient

    with pytest.raises(LLMConfigurationError):
        GroqClient(make_settings())


# ------------------------------------------------------------------- cost


def test_cost_estimate_and_bad_pricing_json() -> None:
    priced = estimate_cost("m", 1_000_000, 500_000, '{"m": {"input": 1, "output": 2}}')
    assert priced.cost_usd == Decimal("2.000000")
    unpriced = estimate_cost("m", 10, 10, "not json")
    assert unpriced.cost_usd == 0 and not unpriced.priced


def test_budget_tracker_refuses_past_limits() -> None:
    b = BudgetTracker(max_tokens=1000, max_cost_usd=Decimal("1"))
    b.check(reserve_tokens=900)
    b.record(TokenUsage(800, 100, Decimal("0.5")))
    with pytest.raises(BudgetExceededError, match="token"):
        b.check(reserve_tokens=200)
    b2 = BudgetTracker(max_tokens=10**9, max_cost_usd=Decimal("1"))
    b2.record(TokenUsage(1, 1, Decimal("1.0")))
    with pytest.raises(BudgetExceededError, match="cost"):
        b2.check()
