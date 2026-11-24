"""Agent I/O contracts.

LLM-facing output models obey the strict-schema rule (see app/llm/schema.py):
every field required, optional values typed ``X | None``, no defaults.
Constraints (lengths, counts) are enforced by Pydantic after the response
arrives; the wire schema carries only structure.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AssignableAgent = Literal["research_agent", "competitor_agent", "market_analysis_agent"]
Priority = Literal["high", "medium", "low"]
ResearchDomain = Literal[
    "market_research",
    "competitor_intelligence",
    "technology_research",
    "business_opportunity",
    "industry_research",
    "product_research",
    "strategic_decision",
]

SourceIds = list[str]

# ------------------------------------------------------------------- planner


class PlannedTask(BaseModel):
    task_id: str = Field(pattern=r"^task_\d{3}$", description="e.g. task_001")
    agent: AssignableAgent
    description: str = Field(min_length=10, max_length=500)
    priority: Priority
    dependencies: list[str] = Field(max_length=10, description="task_ids this task needs first")


class PlanOutput(BaseModel):
    objective: str = Field(min_length=10, max_length=600)
    domain: ResearchDomain
    requirements: list[str] = Field(min_length=1, max_length=8)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=12)


# -------------------------------------------------------------- research agent


class SearchQueries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=4)


class Finding(BaseModel):
    claim: str = Field(min_length=10, max_length=600)
    evidence_type: Literal["fact", "statistic", "estimate", "opinion"]
    importance: Priority
    source_ids: SourceIds = Field(min_length=1, max_length=5)


class ResearchOutput(BaseModel):
    summary: str = Field(max_length=1500)
    findings: list[Finding] = Field(max_length=12)
    gaps: list[str] = Field(max_length=6, description="information that was not found")


# ------------------------------------------------------------ competitor agent


class Competitor(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(max_length=500)
    products: list[str] = Field(max_length=8)
    pricing: str | None = Field(
        max_length=300, description="Only if a source states it; otherwise null (unknown)"
    )
    pricing_source_ids: SourceIds = Field(max_length=4)
    target_customers: str | None = Field(max_length=300)
    positioning: str | None = Field(max_length=300)
    differentiators: list[str] = Field(max_length=6)
    source_ids: SourceIds = Field(min_length=1, max_length=6)


class MarketGap(BaseModel):
    gap: str = Field(min_length=10, max_length=400)
    source_ids: SourceIds = Field(max_length=4)


class CompetitorOutput(BaseModel):
    summary: str = Field(max_length=1500)
    competitors: list[Competitor] = Field(max_length=8)
    market_gaps: list[MarketGap] = Field(max_length=6)
    unknowns: list[str] = Field(max_length=8)


# ---------------------------------------------------------------- market agent

InsightKind = Literal["verified_fact", "estimate", "interpretation", "recommendation"]


class Insight(BaseModel):
    statement: str = Field(min_length=10, max_length=600)
    kind: InsightKind
    category: Literal["trend", "opportunity", "risk", "segment", "pattern"]
    source_ids: SourceIds = Field(max_length=5)
    rationale: str = Field(max_length=500)


class MarketOutput(BaseModel):
    summary: str = Field(max_length=1500)
    insights: list[Insight] = Field(min_length=1, max_length=15)


# ---------------------------------------------------------- verification agent

Verdict = Literal["supported", "partially_supported", "unsupported", "contradicted"]


class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Verdict
    supporting_source_ids: SourceIds = Field(max_length=6)
    contradicts_claim_ids: list[str] = Field(max_length=4)
    note: str = Field(max_length=400)


class VerificationBatchOutput(BaseModel):
    verdicts: list[ClaimVerdict]
    recommended_queries: list[str] = Field(max_length=4)


# ---------------------------------------------------------------- report agent


class CitedPoint(BaseModel):
    text: str = Field(min_length=5, max_length=800)
    citations: SourceIds = Field(max_length=6)


class TitledPoint(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=1200)
    citations: SourceIds = Field(max_length=6)


class ReportCore(BaseModel):
    executive_summary: str = Field(min_length=50, max_length=3000)
    key_findings: list[CitedPoint] = Field(min_length=1, max_length=10)
    market_analysis: str = Field(min_length=20, max_length=4000)
    competitor_analysis: str = Field(min_length=20, max_length=4000)


class ReportStrategy(BaseModel):
    opportunities: list[TitledPoint] = Field(max_length=8)
    risks: list[TitledPoint] = Field(max_length=8)
    recommendations: list[TitledPoint] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(max_length=8)


# ---------------------------------------------------------- internal (no LLM)


class Claim(BaseModel):
    claim_id: str
    text: str
    source_ids: list[str]
    kind: str  # fact | statistic | estimate | opinion | verified_fact | pricing | profile
    origin_task: str
    origin_agent: str
    critical: bool


LLM_OUTPUT_MODELS: tuple[type[BaseModel], ...] = (
    PlanOutput,
    SearchQueries,
    ResearchOutput,
    CompetitorOutput,
    MarketOutput,
    VerificationBatchOutput,
    ReportCore,
    ReportStrategy,
)
