"""Benchmark research tasks.

Each benchmark lists the topics a competent answer must address, with keyword
sets used by the automated coverage/relevance metrics. These are coverage
checklists, not answer keys: Cortex has no ground-truth facts to compare
against, and inventing them would make the evaluation dishonest. Factual
accuracy is assessed by human review (see docs/evaluation.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topic:
    name: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Benchmark:
    id: str
    domain: str
    objective: str
    topics: tuple[Topic, ...]
    focus_areas: tuple[str, ...] = ()
    expects_competitors: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


def T(name: str, *keywords: str) -> Topic:  # compact constructor for the table below
    return Topic(name, tuple(k.lower() for k in keywords))


MARKET_SIZE = T(
    "market size and growth",
    "market size",
    "billion",
    "million",
    "cagr",
    "growth",
    "revenue",
    "valuation",
)
PRICING = T(
    "pricing", "pricing", "price", "per month", "per user", "per seat", "subscription", "tier"
)
REGULATION = T(
    "regulation",
    "regulation",
    "regulatory",
    "compliance",
    "legislation",
    "directive",
    "policy",
    "guideline",
)
RISKS = T("risks", "risk", "challenge", "barrier", "threat", "constraint")

BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark(
        "b01-india-healthcare-saas",
        "market_research",
        "Research the Indian healthcare SaaS market, identify the major competitors, analyse their products and "
        "pricing, identify underserved customer segments, and recommend five business opportunities.",
        (
            MARKET_SIZE,
            T("competitors", "competitor", "vendor", "player", "company"),
            PRICING,
            T(
                "customer segments",
                "clinic",
                "hospital",
                "segment",
                "tier-2",
                "tier 2",
                "small practice",
            ),
        ),
        ("competitors", "pricing", "customer_segments", "opportunities"),
        True,
    ),
    Benchmark(
        "b02-eu-ev-charging",
        "industry_research",
        "Analyse the European public EV charging industry: market structure, leading operators, charging "
        "standards, regulation, and the main risks for new entrants.",
        (
            T("market structure", "operator", "network", "market share", "charge point"),
            T("charging standards", "ccs", "type 2", "standard", "kw", "dc fast"),
            REGULATION,
            RISKS,
        ),
        ("regulation", "risks"),
        True,
    ),
    Benchmark(
        "b03-vector-databases",
        "technology_research",
        "Compare vector database technologies for production retrieval-augmented generation: architecture "
        "approaches, indexing methods, performance trade-offs and managed versus self-hosted options.",
        (
            T("indexing methods", "hnsw", "ivf", "index", "approximate nearest neighbor", "ann"),
            T("performance trade-offs", "latency", "recall", "throughput", "memory", "benchmark"),
            T("deployment options", "managed", "self-hosted", "open source", "cloud"),
        ),
        ("technology",),
        True,
    ),
    Benchmark(
        "b04-india-quick-commerce",
        "competitor_intelligence",
        "Profile the main quick-commerce grocery delivery companies in India: business models, delivery "
        "promises, funding, unit economics and competitive positioning.",
        (
            T("business models", "dark store", "business model", "inventory", "marketplace"),
            T("delivery promise", "minute", "10-minute", "delivery time"),
            T(
                "unit economics",
                "unit economics",
                "margin",
                "order value",
                "profitab",
                "loss",
                "burn",
            ),
        ),
        ("competitors",),
        True,
    ),
    Benchmark(
        "b05-sea-b2b-payments",
        "business_opportunity",
        "Identify business opportunities in B2B cross-border payments for small exporters in Southeast Asia, "
        "including customer pain points, incumbent solutions and regulatory considerations.",
        (
            T("pain points", "fee", "fx", "delay", "pain point", "cost", "settlement"),
            T("incumbents", "bank", "provider", "platform", "competitor"),
            REGULATION,
            T("opportunities", "opportunit", "gap", "underserved"),
        ),
        ("opportunities", "regulation"),
        True,
    ),
    Benchmark(
        "b06-project-mgmt-tools-smb",
        "product_research",
        "Research project management software for small businesses: leading products, feature differences, "
        "pricing models and common user complaints.",
        (
            T("products", "product", "tool", "software", "app"),
            T("features", "feature", "integration", "kanban", "gantt"),
            PRICING,
            T("user complaints", "complaint", "review", "limitation", "drawback"),
        ),
        ("competitors", "pricing"),
        True,
    ),
    Benchmark(
        "b07-cloud-vs-onprem-llm",
        "strategic_decision",
        "Should a mid-sized bank run large language models on-premises or through cloud APIs? Assess cost, "
        "data security, regulatory constraints and operational complexity.",
        (
            T("cost", "cost", "gpu", "pricing", "token", "capex", "opex"),
            T("data security", "security", "data residency", "privacy", "encryption"),
            REGULATION,
            T("operations", "operational", "maintenance", "latency", "skills", "complexity"),
        ),
        ("risks", "regulation"),
        False,
    ),
    Benchmark(
        "b08-plant-based-dairy-us",
        "market_research",
        "Analyse the US plant-based dairy alternatives market: size and growth, product categories, leading "
        "brands, consumer trends and headwinds.",
        (
            MARKET_SIZE,
            T("product categories", "oat", "almond", "soy", "milk", "yogurt", "cheese"),
            T("brands", "brand", "company", "player"),
            T("consumer trends", "consumer", "trend", "demand", "preference"),
        ),
        ("market_size", "trends"),
        True,
    ),
    Benchmark(
        "b09-solar-rooftop-india",
        "industry_research",
        "Research the rooftop solar industry in India for residential customers: government schemes, installed "
        "capacity trends, installer landscape, financing options and adoption barriers.",
        (
            T("government schemes", "scheme", "subsidy", "government", "ministry", "policy"),
            T("capacity", "gw", "mw", "capacity", "installed"),
            T("financing", "loan", "financing", "emi", "credit"),
            RISKS,
        ),
        ("regulation", "trends"),
        False,
    ),
    Benchmark(
        "b10-ai-coding-assistants",
        "competitor_intelligence",
        "Compare AI coding assistants used by professional developers: main products, capabilities, pricing, "
        "enterprise features and adoption evidence.",
        (
            T("products", "assistant", "copilot", "product", "tool"),
            T("capabilities", "completion", "chat", "agent", "code"),
            PRICING,
            T("enterprise", "enterprise", "security", "admin", "sso", "compliance"),
        ),
        ("competitors", "pricing"),
        True,
    ),
    Benchmark(
        "b11-edtech-k12-india",
        "business_opportunity",
        "Evaluate opportunities for a new K-12 education technology product in India after the post-pandemic "
        "correction: market conditions, failed and successful models, and underserved needs.",
        (
            T(
                "market conditions",
                "funding",
                "layoff",
                "valuation",
                "market",
                "decline",
                "correction",
            ),
            T("business models", "model", "subscription", "offline", "hybrid", "tuition"),
            T("underserved needs", "underserved", "vernacular", "rural", "gap", "affordab"),
        ),
        ("opportunities", "customer_segments"),
        True,
    ),
    Benchmark(
        "b12-passkeys-adoption",
        "technology_research",
        "Research the state of passkeys (FIDO2/WebAuthn) for consumer authentication: how they work, platform "
        "support, adoption, usability issues and implementation considerations for a web app.",
        (
            T("how it works", "public key", "webauthn", "fido", "credential", "private key"),
            T("platform support", "apple", "google", "microsoft", "android", "ios", "browser"),
            T("adoption", "adoption", "users", "percent", "%"),
            T("usability", "usability", "recovery", "sync", "friction"),
        ),
        ("technology",),
        False,
    ),
    Benchmark(
        "b13-cold-chain-logistics",
        "industry_research",
        "Analyse the cold chain logistics industry for pharmaceuticals: market drivers, temperature-control "
        "requirements, key service providers, technology adoption and operational risks.",
        (
            T("drivers", "vaccine", "biologic", "demand", "driver", "growth"),
            T("temperature control", "temperature", "°c", "refrigerat", "frozen", "gdp"),
            T("providers", "provider", "logistics", "company", "carrier"),
            RISKS,
        ),
        ("risks", "technology"),
        True,
    ),
    Benchmark(
        "b14-hr-software-mideast",
        "market_research",
        "Research the HR and payroll software market in the Gulf countries (UAE and Saudi Arabia): local "
        "regulations such as wage protection, leading vendors, pricing and gaps for mid-sized companies.",
        (
            T(
                "local regulation",
                "wps",
                "wage protection",
                "labour law",
                "labor law",
                "saudization",
                "regulation",
            ),
            T("vendors", "vendor", "provider", "software", "platform"),
            PRICING,
            T("gaps", "gap", "mid-sized", "sme", "limitation"),
        ),
        ("regulation", "competitors", "pricing"),
        True,
    ),
    Benchmark(
        "b15-build-vs-buy-search",
        "strategic_decision",
        "Should a 50-person e-commerce company build its own product search or buy a hosted search service? "
        "Compare costs, capabilities, time to market and vendor lock-in.",
        (
            T("cost", "cost", "pricing", "price", "engineer", "per month"),
            T("capabilities", "relevance", "typo", "facet", "personaliz", "feature"),
            T("time to market", "time to market", "weeks", "months", "implementation"),
            T("lock-in", "lock-in", "lock in", "migration", "vendor", "portability"),
        ),
        ("risks",),
        True,
    ),
    Benchmark(
        "b16-wearables-health",
        "product_research",
        "Research consumer health wearables beyond smartwatches (rings, patches, continuous glucose monitors "
        "for non-diabetics): products, validation evidence, pricing and regulatory status.",
        (
            T("products", "ring", "patch", "monitor", "device", "wearable"),
            T("validation evidence", "study", "accuracy", "validated", "clinical", "trial"),
            PRICING,
            REGULATION,
        ),
        ("technology", "regulation", "pricing"),
        True,
    ),
)

BY_ID = {b.id: b for b in BENCHMARKS}
assert len(BY_ID) == len(BENCHMARKS) >= 15, "benchmark ids must be unique; at least 15 required"
