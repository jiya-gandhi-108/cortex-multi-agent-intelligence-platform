"""Token usage and cost estimation.

Prices come from ``LLM_PRICING_JSON`` (USD per 1M tokens) so they track the
provider's published price list without code changes. A model with no price
configured is costed at 0 and flagged ``priced=False`` so the UI can say
"cost unknown" rather than show a fabricated number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger

log = get_logger(__name__)
_MILLION = Decimal(1_000_000)


class ModelPrice(BaseModel):
    input: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)


@lru_cache(maxsize=8)
def parse_pricing(raw: str) -> dict[str, ModelPrice]:
    try:
        data = json.loads(raw or "{}")
        return {model: ModelPrice.model_validate(p) for model, p in data.items()}
    except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
        log.error("invalid_llm_pricing_json", error=str(exc))
        return {}


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    priced: bool = True

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cost_usd + other.cost_usd,
            self.priced and other.priced,
        )


def estimate_cost(
    model: str, prompt_tokens: int, completion_tokens: int, pricing_json: str
) -> TokenUsage:
    price = parse_pricing(pricing_json).get(model)
    if price is None:
        return TokenUsage(prompt_tokens, completion_tokens, Decimal(0), priced=False)
    cost = (
        Decimal(prompt_tokens) * price.input + Decimal(completion_tokens) * price.output
    ) / _MILLION
    return TokenUsage(prompt_tokens, completion_tokens, cost.quantize(Decimal("0.000001")))


class BudgetExceededError(Exception):
    """Raised before an LLM call that would start past the project's budget."""


@dataclass
class BudgetTracker:
    """Shared by every node of one run (parallel branches included).

    Checked *before* each call: a call is refused once the budget is spent.
    A single in-flight call may overshoot by at most its own size, which is
    bounded by ``llm_max_prompt_tokens`` + the call's completion cap.
    """

    max_tokens: int
    max_cost_usd: Decimal
    used: TokenUsage = field(default_factory=TokenUsage)

    def check(self, reserve_tokens: int = 0) -> None:
        if self.used.total_tokens + reserve_tokens > self.max_tokens:
            raise BudgetExceededError(
                f"token budget exhausted ({self.used.total_tokens}/{self.max_tokens})"
            )
        if self.used.cost_usd >= self.max_cost_usd:
            raise BudgetExceededError(
                f"cost budget exhausted (${self.used.cost_usd}/${self.max_cost_usd})"
            )

    def record(self, usage: TokenUsage) -> None:
        self.used = self.used + usage
