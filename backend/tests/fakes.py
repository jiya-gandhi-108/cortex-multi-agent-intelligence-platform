"""Deterministic fakes for agent and workflow tests (no network, no API cost)."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.agents.base import RunContext
from app.graph.events import ListSink
from app.llm.base import LLMResult, ModelTier, StructuredRequest
from app.monitoring.cost import BudgetTracker, TokenUsage
from app.schemas.evidence import SourceRecord, citation_key_for
from app.tools.evidence import CollectionResult
from app.tools.url_safety import domain_of, normalize_url
from tests.conftest import make_settings

Handler = Callable[[StructuredRequest], Any]
CITE_LIST = re.compile(r"Cite only these source ids: ([S0-9a-f, ]+)")
CLAIM_LINE = re.compile(r"^- (C-[\w-]+) \(cites ([^)]*)\)", re.M)
DIGEST_IDS = re.compile(r"\[(S[0-9a-f]{6})\]")


def user_text(req: StructuredRequest) -> str:
    return "\n".join(m.content for m in req.messages if m.role == "user")


def cited_ids(req: StructuredRequest) -> list[str]:
    m = CITE_LIST.search(user_text(req))
    return [s.strip() for s in m.group(1).split(",")] if m else []


# ------------------------------------------------------------- default script


def plan_handler(req: StructuredRequest) -> dict[str, Any]:
    return {
        "objective": "Analyse the Indian healthcare SaaS market",
        "domain": "market_research",
        "requirements": ["market size", "competitors"],
        "tasks": [
            {
                "task_id": "task_001",
                "agent": "research_agent",
                "priority": "high",
                "description": "Research Indian healthcare SaaS market size and trends",
                "dependencies": [],
            },
            {
                "task_id": "task_002",
                "agent": "competitor_agent",
                "priority": "high",
                "description": "Identify major Indian healthcare SaaS competitors and pricing",
                "dependencies": [],
            },
            {
                "task_id": "task_003",
                "agent": "market_analysis_agent",
                "priority": "medium",
                "description": "Analyse opportunities and underserved segments from evidence",
                "dependencies": ["task_001", "task_002"],
            },
        ],
    }


def queries_handler(req: StructuredRequest) -> dict[str, Any]:
    return {"queries": ["india healthcare saas market size", "healthcare saas india trends"]}


def research_handler(req: StructuredRequest) -> dict[str, Any]:
    ids = cited_ids(req)
    return {
        "summary": "The market is growing.",
        "findings": [
            {
                "claim": "Indian healthcare SaaS market grew strongly in 2025.",
                "evidence_type": "statistic",
                "importance": "high",
                "source_ids": ids[:2],
            },
            {
                "claim": "Clinics are adopting cloud EMR systems quickly.",
                "evidence_type": "fact",
                "importance": "medium",
                "source_ids": [ids[-1]],
            },
            {
                "claim": "A claim citing only a fabricated source id here.",
                "evidence_type": "fact",
                "importance": "low",
                "source_ids": ["Sdeadbe"],
            },
        ],
        "gaps": ["rural adoption data"],
    }


def competitor_handler(req: StructuredRequest) -> dict[str, Any]:
    ids = cited_ids(req)
    return {
        "summary": "Several competitors operate.",
        "competitors": [
            {
                "name": "Practo",
                "description": "Clinic software and marketplace",
                "products": ["Practo Ray"],
                "pricing": "INR 999/month",
                "pricing_source_ids": [ids[0]],
                "target_customers": "clinics",
                "positioning": "all-in-one",
                "differentiators": ["network"],
                "source_ids": [ids[0]],
            },
            {
                "name": "GuessCo",
                "description": "EMR vendor",
                "products": ["EMR"],
                "pricing": "$50/user",
                "pricing_source_ids": [],
                "target_customers": None,
                "positioning": None,
                "differentiators": [],
                "source_ids": [ids[-1]],
            },
        ],
        "market_gaps": [{"gap": "Few tools target small rural clinics.", "source_ids": [ids[-1]]}],
        "unknowns": ["enterprise pricing"],
    }


def market_handler(req: StructuredRequest) -> dict[str, Any]:
    ids = cited_ids(req)
    return {
        "summary": "Opportunities in rural clinics.",
        "insights": [
            {
                "statement": "Cloud EMR adoption is rising among clinics.",
                "kind": "verified_fact",
                "category": "trend",
                "source_ids": ids[:1],
                "rationale": "stated by source",
            },
            {
                "statement": "Rural clinics are the largest untapped segment overall.",
                "kind": "verified_fact",
                "category": "segment",
                "source_ids": [],
                "rationale": "pattern across findings",
            },
            {
                "statement": "The market may double by 2030 per one analyst.",
                "kind": "estimate",
                "category": "trend",
                "source_ids": ids[-1:],
                "rationale": "analyst estimate",
            },
            {
                "statement": "Build a low-cost EMR for rural clinics.",
                "kind": "recommendation",
                "category": "opportunity",
                "source_ids": ids[:1],
                "rationale": "gap plus adoption trend",
            },
        ],
    }


def verify_all_supported(req: StructuredRequest) -> dict[str, Any]:
    verdicts = [
        {
            "claim_id": cid,
            "verdict": "supported",
            "supporting_source_ids": [s.strip() for s in cites.split(",") if s.strip()],
            "contradicts_claim_ids": [],
            "note": "stated",
        }
        for cid, cites in CLAIM_LINE.findall(user_text(req))
    ]
    return {"verdicts": verdicts, "recommended_queries": []}


def verify_all_unsupported(req: StructuredRequest) -> dict[str, Any]:
    verdicts = [
        {
            "claim_id": cid,
            "verdict": "unsupported",
            "supporting_source_ids": [],
            "contradicts_claim_ids": [],
            "note": "not in excerpt",
        }
        for cid, _ in CLAIM_LINE.findall(user_text(req))
    ]
    return {"verdicts": verdicts, "recommended_queries": ["india healthcare saas revenue 2025"]}


def report_core_handler(req: StructuredRequest) -> dict[str, Any]:
    ids = list(dict.fromkeys(DIGEST_IDS.findall(user_text(req))))
    a, b = ids[0], ids[-1]
    return {
        "executive_summary": f"The Indian healthcare SaaS market is growing [{a}] with gaps in rural "
        f"clinics [{b}]. A fabricated citation [Sdeadbe] must be removed.",
        "key_findings": [
            {"text": f"Strong growth in 2025 [{a}].", "citations": [a]},
            {"text": "An uncited finding that should be dropped.", "citations": ["Sdeadbe"]},
        ],
        "market_analysis": f"Adoption of cloud EMR is rising [{a}].",
        "competitor_analysis": f"Practo leads the clinic segment [{a}].",
    }


def report_strategy_handler(req: StructuredRequest) -> dict[str, Any]:
    ids = list(dict.fromkeys(DIGEST_IDS.findall(user_text(req))))
    return {
        "opportunities": [
            {
                "title": "Rural clinics",
                "description": f"Underserved segment [{ids[-1]}].",
                "citations": [ids[-1]],
            }
        ],
        "risks": [
            {
                "title": "Competition",
                "description": f"Incumbents are strong [{ids[0]}].",
                "citations": [ids[0]],
            }
        ],
        "recommendations": [
            {
                "title": "Launch low-cost EMR",
                "description": f"Target rural clinics [{ids[-1]}].",
                "citations": [ids[-1]],
            }
        ],
        "limitations": ["Limited public data on rural adoption."],
    }


DEFAULT_HANDLERS: dict[str, Handler] = {
    "PlanOutput": plan_handler,
    "SearchQueries": queries_handler,
    "ResearchOutput": research_handler,
    "CompetitorOutput": competitor_handler,
    "MarketOutput": market_handler,
    "VerificationBatchOutput": verify_all_supported,
    "ReportCore": report_core_handler,
    "ReportStrategy": report_strategy_handler,
}


class FakeLLM:
    provider = "fake"

    def __init__(
        self, overrides: dict[str, Handler] | None = None, tokens: tuple[int, int] = (300, 150)
    ) -> None:
        self.handlers = {**DEFAULT_HANDLERS, **(overrides or {})}
        self.requests: list[StructuredRequest] = []
        self.tokens = tokens

    def model_for(self, tier: ModelTier) -> str:
        return f"fake-{tier.value}"

    def calls(self, schema: str) -> list[StructuredRequest]:
        return [r for r in self.requests if r.schema_name == schema]

    async def complete_structured(self, request: StructuredRequest) -> LLMResult:
        self.requests.append(request)
        await asyncio.sleep(0)
        out = self.handlers[request.schema_name](request)
        if isinstance(out, Exception):
            raise out
        content = out if isinstance(out, str) else json.dumps(out)
        p, c = self.tokens
        return LLMResult(
            content,
            self.model_for(request.tier),
            TokenUsage(p, c, Decimal("0.0001")),
            latency_ms=5.0,
        )


# ---------------------------------------------------------------- collector


def make_source(
    url: str, task_key: str, content: str = "", days_old: int = 30, fetched: bool = True
) -> SourceRecord:
    norm = normalize_url(url)
    return SourceRecord(
        citation_key=citation_key_for(norm),
        url=url,
        normalized_url=norm,
        title=f"Title for {url}",
        domain=domain_of(url),
        publication_date=datetime.now(UTC) - timedelta(days=days_old),
        retrieved_at=datetime.now(UTC),
        content=content or f"Evidence text from {url} about Indian healthcare SaaS growth.",
        fetched=fetched,
        task_key=task_key,
        search_provider="fake",
        query="q",
    )


class FakeCollector:
    """Returns 3 distinct-domain sources per task; optional hooks for failure tests."""

    def __init__(
        self, per_task: int = 3, on_collect: Callable[[str], None] | None = None, content: str = ""
    ) -> None:
        self.per_task = per_task
        self.on_collect = on_collect
        self.content = content
        self.calls: list[tuple[str, list[str]]] = []

    async def collect(
        self,
        task_key: str,
        queries: list[str],
        known_urls: set[str] | None = None,
        limit: int | None = None,
        on_tool_call: Any = None,
    ) -> CollectionResult:
        self.calls.append((task_key, queries))
        await asyncio.sleep(0.01)  # yield so parallel branches interleave
        if self.on_collect:
            self.on_collect(task_key)
        sources = [
            make_source(f"https://site{i}-{task_key}.example.com/report", task_key, self.content)
            for i in range(self.per_task)
        ]
        if on_tool_call:
            for q in queries:
                on_tool_call("web_search", {"query": q, "results": self.per_task})
        return CollectionResult(sources=sources, queries_run=len(queries))


def make_context(
    llm: FakeLLM | None = None,
    collector: Any = None,
    *,
    max_tokens: int = 1_000_000,
    **settings: Any,
) -> tuple[RunContext, ListSink]:
    sink = ListSink()
    ctx = RunContext(
        project_id="proj-1",
        settings=make_settings(**settings),
        llm=llm or FakeLLM(),
        collector=collector or FakeCollector(),
        budget=BudgetTracker(max_tokens=max_tokens, max_cost_usd=Decimal("100")),
        sink=sink,
    )
    return ctx, sink


LIMITS = {
    "max_research_iterations": 1,
    "max_agent_retries": 1,
    "require_plan_approval": False,
    "max_tokens": 1_000_000,
    "max_cost_usd": 100.0,
}
