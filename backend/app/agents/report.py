"""Report Generation Agent + quality check.

The LLM writes prose sections from *verified* claims only (supported or
partially supported). Everything factual that can be rendered from data is
rendered deterministically: methodology, the competitor comparison table,
the evidence-classified insights, the source list and the evidence-based
limitations. Citations are validated against retrieved sources; invalid ones
are removed and counted, and uncited key findings are dropped.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.agents.base import CallLog, RunContext, structured_call, system_prompt
from app.llm.base import ModelTier
from app.schemas.agents import Claim, ReportCore, ReportStrategy
from app.schemas.evidence import SourceRecord

AGENT = "report_agent"
CITE_RE = re.compile(r"\[(S[0-9a-f]{6})\]")
DIGEST_CHARS = 9_000

ROLE = """You are the report agent. Write a professional research report from the verified
evidence provided. Rules:
- Use only the claims, competitor data and insights provided. Do not add facts.
- Cite with source ids in square brackets inline, e.g. "grew 20% [S3f9a2c]", and list the
  same ids in each point's citations.
- Claims marked (partial) are only partly supported: phrase them cautiously.
- Keep the distinction between verified facts, estimates and interpretations explicit.
- Be concise and specific; prefer concrete statements over generic advice."""

STRATEGY_ROLE = """You are the report agent writing the strategy sections of a research report.
From the verified evidence provided, write opportunities, risks and actionable strategic
recommendations, each grounded in cited evidence (source ids in square brackets inline and
in citations). Recommendations are your interpretation of the evidence: make the reasoning
visible. In limitations, state what the evidence does not cover. Do not add facts."""


# ---------------------------------------------------------------- digest


def build_digest(
    claims: list[Claim],
    verdicts: dict[str, dict[str, Any]],
    outputs: dict[str, dict[str, Any]],
    verification: dict[str, Any],
) -> str:
    usable = []
    for c in claims:
        v = verdicts.get(c.claim_id, {}).get("verdict")
        if v in ("supported", "partially_supported"):
            ids = verdicts[c.claim_id]["supporting_source_ids"] or c.source_ids
            mark = " (partial)" if v == "partially_supported" else ""
            usable.append(f"- [{c.kind}]{mark} {c.text} {' '.join(f'[{i}]' for i in ids)}")
    competitors, insights = [], []
    for out in outputs.values():
        for comp in out.get("competitors", []):
            competitors.append(
                {
                    "name": comp["name"],
                    "positioning": comp.get("positioning"),
                    "target_customers": comp.get("target_customers"),
                    "pricing": comp.get("pricing") or "unknown",
                    "differentiators": comp.get("differentiators", []),
                    "sources": comp["source_ids"],
                }
            )
        for gap in out.get("market_gaps", []):
            insights.append(
                f"- [market gap] {gap['gap']} {' '.join(f'[{i}]' for i in gap['source_ids'])}"
            )
        for ins in out.get("insights", []):
            insights.append(
                f"- [{ins['kind']}/{ins['category']}] {ins['statement']} "
                + " ".join(f"[{i}]" for i in ins["source_ids"])
            )
    parts = [
        "VERIFIED CLAIMS:\n" + ("\n".join(usable) or "(none)"),
        "COMPETITORS:\n" + json.dumps(competitors, ensure_ascii=False, separators=(",", ":")),
        "ANALYST INSIGHTS:\n" + ("\n".join(insights) or "(none)"),
        f"EVIDENCE STATUS: score {verification.get('score')} (heuristic), "
        f"sufficient={verification.get('sufficient')}; "
        f"issues: {'; '.join(verification.get('reasons', [])) or 'none'}",
    ]
    digest = "\n\n".join(parts)
    return digest[:DIGEST_CHARS] + ("\n…(truncated)" if len(digest) > DIGEST_CHARS else "")


# ------------------------------------------------------------- citations


def clean_citations(text: str, allowed: set[str]) -> tuple[str, int]:
    removed = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal removed
        if m.group(1) in allowed:
            return m.group(0)
        removed += 1
        return ""

    cleaned = CITE_RE.sub(repl, text)
    return re.sub(r" {2,}", " ", cleaned).strip(), removed


def _clean_list(ids: list[str], allowed: set[str]) -> tuple[list[str], int]:
    kept = [i for i in dict.fromkeys(ids) if i in allowed]
    return kept, len(set(ids)) - len(kept)


def validate_report(
    core: ReportCore, strategy: ReportStrategy, allowed: set[str]
) -> tuple[ReportCore, ReportStrategy, dict[str, int]]:
    invalid = dropped = 0
    core.executive_summary, n = clean_citations(core.executive_summary, allowed)
    invalid += n
    core.market_analysis, n = clean_citations(core.market_analysis, allowed)
    invalid += n
    core.competitor_analysis, n = clean_citations(core.competitor_analysis, allowed)
    invalid += n
    findings = []
    for f in core.key_findings:
        f.text, n = clean_citations(f.text, allowed)
        f.citations, m = _clean_list(f.citations + CITE_RE.findall(f.text), allowed)
        invalid += n + m
        if f.citations:
            findings.append(f)
        else:
            dropped += 1
    core.key_findings = findings
    for group in (strategy.opportunities, strategy.risks, strategy.recommendations):
        for p in group:
            p.description, n = clean_citations(p.description, allowed)
            p.citations, m = _clean_list(p.citations + CITE_RE.findall(p.description), allowed)
            invalid += n + m
    return (
        core,
        strategy,
        {"invalid_citations_removed": invalid, "uncited_findings_dropped": dropped},
    )


def cited_keys(core: ReportCore, strategy: ReportStrategy) -> list[str]:
    keys: list[str] = []
    texts = [core.executive_summary, core.market_analysis, core.competitor_analysis]
    for f in core.key_findings:
        keys += f.citations
        texts.append(f.text)
    for group in (strategy.opportunities, strategy.risks, strategy.recommendations):
        for p in group:
            keys += p.citations
            texts.append(p.description)
    for t in texts:
        keys += CITE_RE.findall(t)
    return list(dict.fromkeys(keys))


# ------------------------------------------------------------------ LLM


async def write_report(
    ctx: RunContext, log: CallLog, *, objective: str, digest: str
) -> tuple[ReportCore, ReportStrategy]:
    user = f"<objective>\n{objective}\n</objective>\n\n<evidence>\n{digest}\n</evidence>"
    core = await structured_call(
        ctx,
        log,
        agent=AGENT,
        output_model=ReportCore,
        system=system_prompt(ROLE),
        user=user + "\n\nWrite the executive summary, key findings, market analysis and "
        "competitor analysis.",
        tier=ModelTier.REASONING,
        max_completion_tokens=3_000,
    )
    strategy = await structured_call(
        ctx,
        log,
        agent=AGENT,
        output_model=ReportStrategy,
        system=system_prompt(STRATEGY_ROLE),
        user=user + "\n\nWrite opportunities, risks, strategic recommendations and limitations.",
        tier=ModelTier.REASONING,
        max_completion_tokens=3_000,
    )
    return core, strategy


# --------------------------------------------------------------- render


AGENT_LABELS = {
    "research_agent": "Research",
    "competitor_agent": "Competitor intelligence",
    "market_analysis_agent": "Market analysis",
}


def _extra_cites(text: str, ids: list[str], sources: dict[str, SourceRecord]) -> str:
    """Citations listed for a point but not already cited inline in its text."""
    inline = set(CITE_RE.findall(text))
    return _cite([i for i in ids if i not in inline], sources)


def _cite(ids: list[str], sources: dict[str, SourceRecord]) -> str:
    return " ".join(f"[[{i}]]({sources[i].url})" for i in ids if i in sources)


def _inline(text: str, sources: dict[str, SourceRecord]) -> str:
    return CITE_RE.sub(lambda m: _cite([m.group(1)], sources), text)


def _md_cell(value: Any) -> str:
    if value in (None, "", []):
        return "Unknown"
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(
    *,
    title: str,
    objective: str,
    core: ReportCore,
    strategy: ReportStrategy,
    sources: dict[str, SourceRecord],
    cited: list[str],
    tasks: dict[str, dict[str, Any]],
    outputs: dict[str, dict[str, Any]],
    verification: dict[str, Any],
    stats: dict[str, Any],
    extra_limitations: list[str],
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    counts = verification.get("counts", {})
    lines = [
        f"# {title}",
        "",
        f"*Generated {now} · Evidence score {verification.get('score', 0):.2f} "
        "(heuristic index, not a probability)*",
        "",
    ]
    if not verification.get("sufficient"):
        lines += [
            "> **Evidence warning:** verification judged the evidence insufficient: "
            + "; ".join(verification.get("reasons", []))
            + ". Treat conclusions as provisional.",
            "",
        ]

    lines += ["## 1. Executive Summary", "", _inline(core.executive_summary, sources), ""]
    lines += ["## 2. Research Objective", "", objective, ""]

    lines += [
        "## 3. Methodology",
        "",
        f"Cortex decomposed the objective into {len(tasks)} tasks executed by specialised "
        f"agents, ran {stats.get('queries', 0)} web searches and retrieved "
        f"{len(sources)} sources ({stats.get('fetched', 0)} full pages, "
        f"{len(sources) - stats.get('fetched', 0)} search snippets). "
        f"{counts.get('total', 0)} claims were checked against their cited sources "
        f"({counts.get('supported', 0)} supported, {counts.get('partially_supported', 0)} "
        f"partially supported, {counts.get('unsupported', 0)} unsupported, "
        f"{counts.get('contradicted', 0)} contradicted). Only supported and partially "
        "supported claims were used in this report.",
        "",
        "| Task | Agent | Description | Status |",
        "|---|---|---|---|",
    ]
    for key, t in sorted(tasks.items()):
        agent = AGENT_LABELS.get(t["agent"], t["agent"])
        lines.append(f"| {key} | {agent} | {_md_cell(t['description'])} | {t['status']} |")
    lines.append("")

    lines += ["## 4. Key Findings", ""]
    lines += [
        f"- {_inline(f.text, sources)} {_extra_cites(f.text, f.citations, sources)}".rstrip()
        for f in core.key_findings
    ] or ["- No findings met the evidence bar."]
    lines.append("")

    lines += ["## 5. Market Analysis", "", _inline(core.market_analysis, sources), ""]
    insights = [i for o in outputs.values() for i in o.get("insights", [])]
    if insights:
        labels = {
            "verified_fact": "Verified fact",
            "estimate": "Estimate",
            "interpretation": "Interpretation",
            "recommendation": "Recommendation",
        }
        lines += ["**Evidence classification**", ""]
        lines += [
            f"- *{labels[i['kind']]}* ({i['category']}): {i['statement']} "
            f"{_cite(i['source_ids'], sources)}".rstrip()
            for i in insights
        ]
        lines.append("")

    lines += ["## 6. Competitor Analysis", "", _inline(core.competitor_analysis, sources), ""]
    competitors = [c for o in outputs.values() for c in o.get("competitors", [])]
    if competitors:
        lines += [
            "| Company | Products | Pricing | Target customers | Positioning | Sources |",
            "|---|---|---|---|---|---|",
        ]
        for c in competitors:
            pricing = (
                f"{_md_cell(c['pricing'])} {_cite(c['pricing_source_ids'], sources)}"
                if c.get("pricing")
                else "Unknown"
            )
            lines.append(
                f"| {_md_cell(c['name'])} | {_md_cell(c.get('products'))} | {pricing} | "
                f"{_md_cell(c.get('target_customers'))} | {_md_cell(c.get('positioning'))} | "
                f"{_cite(c['source_ids'], sources)} |"
            )
        lines.append("")

    for number, heading, group in (
        (7, "Opportunities", strategy.opportunities),
        (8, "Risks", strategy.risks),
        (9, "Strategic Recommendations", strategy.recommendations),
    ):
        lines += [f"## {number}. {heading}", ""]
        if not group:
            lines += ["None identified from the available evidence.", ""]
        for i, p in enumerate(group, 1):
            lines += [
                f"**{i}. {p.title}**",
                "",
                f"{_inline(p.description, sources)} "
                f"{_extra_cites(p.description, p.citations, sources)}".rstrip(),
                "",
            ]

    lines += ["## 10. Sources", ""]
    for n, key in enumerate(cited, 1):
        s = sources[key]
        published = s.publication_date.date().isoformat() if s.publication_date else "date unknown"
        note = "" if s.fetched else " · search snippet only"
        lines.append(
            f"{n}. **[{key}]** {s.title or s.domain} — {s.domain}, {published} "
            f"(retrieved {s.retrieved_at.date().isoformat()}{note}). <{s.url}>"
        )
    if not cited:
        lines.append("No sources were cited.")
    lines.append("")

    lines += ["## 11. Research Limitations", ""]
    lines += [f"- {item}" for item in [*strategy.limitations, *extra_limitations]] or [
        "- None noted."
    ]
    lines.append("")
    return "\n".join(lines)


def evidence_limitations(
    verification: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    sources: dict[str, SourceRecord],
    cited: list[str],
) -> list[str]:
    out = []
    counts = verification.get("counts", {})
    if counts.get("unsupported") or counts.get("contradicted"):
        out.append(
            f"{counts.get('unsupported', 0)} claims were unsupported and "
            f"{counts.get('contradicted', 0)} contradicted by sources; they were excluded."
        )
    failed = [k for k, t in tasks.items() if t["status"] in ("failed", "skipped")]
    if failed:
        out.append(
            f"Tasks not completed: {', '.join(sorted(failed))}; their topics may be under-covered."
        )
    snippet_only = [k for k in cited if not sources[k].fetched]
    if snippet_only:
        out.append(f"{len(snippet_only)} cited sources could only be read as search snippets.")
    outdated = [k for k in verification.get("outdated_sources", []) if k in cited]
    if outdated:
        out.append(
            f"{len(outdated)} cited sources are older than "
            f"{verification['criteria']['source_max_age_years']} years."
        )
    if any(s.publication_date is None for k, s in sources.items() if k in cited):
        out.append("Some sources have no detectable publication date.")
    out.append(
        "Evidence was limited to publicly accessible web pages; paywalled and "
        "proprietary data were not available."
    )
    return out


# --------------------------------------------------------------- quality


def quality_check(
    core: ReportCore,
    strategy: ReportStrategy,
    cited: list[str],
    verification: dict[str, Any],
    validation: dict[str, int],
    tasks: dict[str, dict[str, Any]],
    sources: dict[str, SourceRecord],
) -> dict[str, Any]:
    hard, warnings = [], []
    if len(core.executive_summary.strip()) < 50:
        hard.append("executive summary missing or too short")
    if not core.key_findings:
        hard.append("no key findings with valid citations")
    if not cited:
        hard.append("report cites no retrieved sources")
    if not strategy.recommendations:
        hard.append("no strategic recommendations")
    if not verification.get("sufficient"):
        warnings.append(
            "evidence judged insufficient: " + "; ".join(verification.get("reasons", []))
        )
    if validation.get("invalid_citations_removed"):
        warnings.append(f"{validation['invalid_citations_removed']} invalid citations removed")
    if validation.get("uncited_findings_dropped"):
        warnings.append(f"{validation['uncited_findings_dropped']} uncited findings dropped")
    failed = [k for k, t in tasks.items() if t["status"] in ("failed", "skipped")]
    if failed:
        warnings.append(f"incomplete tasks: {', '.join(sorted(failed))}")
    if cited and sum(not sources[k].fetched for k in cited) / len(cited) > 0.5:
        warnings.append("more than half of cited sources are search snippets only")
    return {"passed": not hard, "hard_failures": hard, "warnings": warnings}
