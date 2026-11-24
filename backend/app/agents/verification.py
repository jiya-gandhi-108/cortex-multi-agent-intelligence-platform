"""Verification Agent.

Two layers:
  1. LLM judgement: each claim is checked against the excerpts of the sources it
     cites (batched to fit the prompt ceiling) -> supported / partially_supported /
     unsupported / contradicted, plus contradictions between claims in the batch.
  2. Deterministic scoring (documented in docs/agent-design.md). The score is a
     heuristic evidence-strength index in [0, 1], NOT a calibrated probability.

    claim_score = verdict_base x corroboration x freshness
      verdict_base:   supported 1.0 | partially_supported 0.5 | unsupported 0 | contradicted 0
      corroboration:  distinct supporting domains 1 -> 0.7, 2 -> 0.9, >=3 -> 1.0
      freshness:      newest supporting source within max age -> 1.0,
                      all dated sources older -> 0.8, no dates known -> 0.9

    reliability_score = weighted mean over ALL claims (critical weight 2): how much
                        of what the agents asserted held up. Reported, not gating.
    evidence_score    = weighted mean over USABLE claims (supported / partially
                        supported): the strength of what the report stands on.

    sufficient  <=>  evidence_score >= 0.60
                AND  usable claims >= 3
                AND  distinct sources behind usable claims >= depth minimum
                AND  critical claims checked in the LATEST round that were
                     unsupported/contradicted <= 30%

  Scoping the critical-failure test to the latest round lets a follow-up research
  round be judged on its own evidence; claims that failed earlier are excluded
  from the report either way.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agents.base import (
    CallLog,
    RunContext,
    prompt_char_budget,
    render_sources,
    structured_call,
    system_prompt,
)
from app.agents.profiles import DepthProfile
from app.llm.base import ModelTier
from app.schemas.agents import Claim, ClaimVerdict, VerificationBatchOutput
from app.schemas.evidence import SourceRecord

AGENT = "verification_agent"
BATCH_SIZE = 8
SCORE_THRESHOLD = 0.60
MAX_CRITICAL_FAILURE_RATE = 0.30
MIN_CLAIMS = 3
VERDICT_BASE = {
    "supported": 1.0,
    "partially_supported": 0.5,
    "unsupported": 0.0,
    "contradicted": 0.0,
}

ROLE = """You are the verification agent. For each claim, judge ONLY whether the excerpts of
the sources provided support it:
- supported: the excerpts state it (numbers, names and scope match)
- partially_supported: the excerpts support part of it, or a weaker/older version of it
- unsupported: the excerpts do not state it (even if it might be true)
- contradicted: an excerpt states something incompatible with it
List in supporting_source_ids only sources whose excerpts actually support the claim.
If two claims in this batch contradict each other, list the other claim id in
contradicts_claim_ids. Keep notes short and specific. You may suggest up to 4 search
queries that would find missing evidence. Return exactly one verdict per claim id."""


# ----------------------------------------------------------------- claims


def build_claims(task_key: str, agent: str, output: dict[str, Any], priority: str) -> list[Claim]:
    """Deterministically turn agent output into checkable claims."""
    claims: list[Claim] = []
    high_task = priority == "high"

    def add(text: str, ids: list[str], kind: str, critical: bool) -> None:
        if ids:
            claims.append(
                Claim(
                    claim_id=f"C-{task_key}-{len(claims) + 1}",
                    text=text,
                    source_ids=ids,
                    kind=kind,
                    origin_task=task_key,
                    origin_agent=agent,
                    critical=critical,
                )
            )

    for f in output.get("findings", []):
        add(
            f["claim"],
            f["source_ids"],
            f["evidence_type"],
            f["importance"] == "high" or (high_task and f["evidence_type"] == "statistic"),
        )
    for c in output.get("competitors", []):
        profile = c["name"] + (f": {c['description']}" if c.get("description") else "")
        if c.get("products"):
            profile += f" Products: {', '.join(c['products'])}."
        add(profile, c["source_ids"], "profile", False)
        if c.get("pricing"):
            add(f"{c['name']} pricing: {c['pricing']}", c["pricing_source_ids"], "pricing", True)
    for ins in output.get("insights", []):
        if ins["kind"] in ("verified_fact", "estimate"):
            add(ins["statement"], ins["source_ids"], ins["kind"], ins["kind"] == "verified_fact")
    return claims


# ------------------------------------------------------------------ LLM pass


def _batch_validator(expected: set[str]) -> Callable[[VerificationBatchOutput], list[str]]:
    def validate(out: VerificationBatchOutput) -> list[str]:
        got = [v.claim_id for v in out.verdicts]
        errs = []
        missing = sorted(expected - set(got))
        extra = sorted(set(got) - expected)
        dupes = sorted({c for c in got if got.count(c) > 1})
        if missing:
            errs.append(f"missing verdicts for {missing}")
        if extra:
            errs.append(f"unknown claim ids {extra}")
        if dupes:
            errs.append(f"duplicate verdicts for {dupes}")
        return errs

    return validate


async def verify_claims(
    ctx: RunContext,
    log: CallLog,
    *,
    claims: list[Claim],
    sources: dict[str, SourceRecord],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    verdicts: dict[str, dict[str, Any]] = {}
    queries: list[str] = []
    for start in range(0, len(claims), BATCH_SIZE):
        ctx.check_cancelled()
        batch = claims[start : start + BATCH_SIZE]
        cited_keys = list(dict.fromkeys(k for c in batch for k in c.source_ids if k in sources))
        batch_sources = [sources[k] for k in cited_keys]
        claims_text = "\n".join(
            f"- {c.claim_id} (cites {', '.join(c.source_ids)}): {c.text}" for c in batch
        )
        head = f"<claims>\n{claims_text}\n</claims>\n\n<sources>\n"
        tail = "\n</sources>"
        budget = prompt_char_budget(ctx.settings, len(head) + len(tail) + 2_000)
        per_source = max(300, min(900, budget // max(1, len(batch_sources))))
        user = head + render_sources(batch_sources, per_source, budget) + tail

        out = await structured_call(
            ctx,
            log,
            agent=AGENT,
            output_model=VerificationBatchOutput,
            system=system_prompt(ROLE),
            user=user,
            tier=ModelTier.REASONING,
            max_completion_tokens=2_000,
            validate=_batch_validator({c.claim_id for c in batch}),
        )
        allowed = set(cited_keys)
        batch_ids = {c.claim_id for c in batch}
        for v in out.verdicts:
            v.supporting_source_ids = [k for k in v.supporting_source_ids if k in allowed]
            v.contradicts_claim_ids = [c for c in v.contradicts_claim_ids if c in batch_ids]
            if v.verdict in ("supported", "partially_supported") and not v.supporting_source_ids:
                v.verdict = "unsupported"  # support must point at real evidence
                v.note = (v.note + " [no valid supporting source]").strip()
            verdicts[v.claim_id] = v.model_dump()
        queries.extend(out.recommended_queries)
    return verdicts, list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:6]


# --------------------------------------------------------------- scoring


def corroboration(domains: int) -> float:
    return 0.0 if domains <= 0 else 0.7 if domains == 1 else 0.9 if domains == 2 else 1.0


def freshness(dates: list[datetime | None], max_age_years: int, now: datetime) -> float:
    known = [d for d in dates if d is not None]
    if not known:
        return 0.9
    cutoff = now - timedelta(days=365 * max_age_years)
    return 1.0 if max(known) >= cutoff else 0.8


def _claim_score(
    claim: Claim,
    v: dict[str, Any] | None,
    sources: dict[str, SourceRecord],
    max_age: int,
    now: datetime,
) -> float:
    verdict = v["verdict"] if v else "unsupported"
    base = VERDICT_BASE[verdict]
    if not base:
        return 0.0
    support = [sources[k] for k in v["supporting_source_ids"] if k in sources] if v else []
    return (
        base
        * corroboration(len({s.domain for s in support}))
        * freshness([s.publication_date for s in support], max_age, now)
    )


def _weighted(claims: list[Claim], scores: dict[str, float]) -> float:
    total = sum(2.0 if c.critical else 1.0 for c in claims)
    if not total:
        return 0.0
    return round(sum((2.0 if c.critical else 1.0) * scores[c.claim_id] for c in claims) / total, 3)


def score_verification(
    claims: list[Claim],
    verdicts: dict[str, dict[str, Any]],
    sources: dict[str, SourceRecord],
    profile: DepthProfile,
    max_age_years: int,
    recommended_queries: list[str],
    gaps: list[str],
    latest_round: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    verdict_of = {
        c.claim_id: (verdicts.get(c.claim_id) or {}).get("verdict", "unsupported") for c in claims
    }
    scores = {
        c.claim_id: round(_claim_score(c, verdicts.get(c.claim_id), sources, max_age_years, now), 3)
        for c in claims
    }
    counts = {k: 0 for k in VERDICT_BASE}
    for v in verdict_of.values():
        counts[v] += 1

    usable = [c for c in claims if verdict_of[c.claim_id] in ("supported", "partially_supported")]
    reliability = _weighted(claims, scores)
    evidence = _weighted(usable, scores)
    usable_sources = {
        k for c in usable for k in verdicts[c.claim_id]["supporting_source_ids"] if k in sources
    }
    round_ids = latest_round if latest_round is not None else {c.claim_id for c in claims}
    round_critical = [c for c in claims if c.critical and c.claim_id in round_ids]
    round_failed = [
        c for c in round_critical if verdict_of[c.claim_id] in ("unsupported", "contradicted")
    ]
    critical_rate = round(len(round_failed) / len(round_critical), 3) if round_critical else 0.0

    reasons = []
    if evidence < SCORE_THRESHOLD:
        reasons.append(f"evidence score {evidence} below {SCORE_THRESHOLD}")
    if len(usable) < MIN_CLAIMS:
        reasons.append(f"only {len(usable)} verified claims (minimum {MIN_CLAIMS})")
    if len(usable_sources) < profile.min_distinct_sources:
        reasons.append(
            f"only {len(usable_sources)} sources support verified claims "
            f"(minimum {profile.min_distinct_sources})"
        )
    if critical_rate > MAX_CRITICAL_FAILURE_RATE:
        reasons.append(
            f"{len(round_failed)}/{len(round_critical)} critical claims in the latest round "
            "unsupported or contradicted"
        )

    contradictions = [
        {"claim_id": cid, "contradicts": v["contradicts_claim_ids"], "note": v["note"]}
        for cid, v in verdicts.items()
        if cid in verdict_of and (v["verdict"] == "contradicted" or v["contradicts_claim_ids"])
    ]
    cutoff = now - timedelta(days=365 * max_age_years)
    outdated = sorted(
        k for k, s in sources.items() if s.publication_date and s.publication_date < cutoff
    )
    weak = {cid for cid, v in verdict_of.items() if v != "supported"}
    needs_check = sorted(
        {k for c in claims if c.claim_id in weak for k in c.source_ids if k in sources}
        | {
            k
            for c in claims
            if c.critical
            for k in c.source_ids
            if k in sources and not sources[k].fetched
        }
    )
    return {
        "score": evidence,
        "evidence_score": evidence,
        "reliability_score": reliability,
        "sufficient": not reasons,
        "reasons": reasons,
        "criteria": {
            "score_threshold": SCORE_THRESHOLD,
            "max_critical_failure_rate": MAX_CRITICAL_FAILURE_RATE,
            "min_distinct_sources": profile.min_distinct_sources,
            "min_claims": MIN_CLAIMS,
            "source_max_age_years": max_age_years,
            "note": "Heuristic evidence-strength index, not a calibrated probability.",
        },
        "counts": {
            **counts,
            "total": len(claims),
            "usable": len(usable),
            "critical_latest_round": len(round_critical),
            "critical_failed_latest_round": len(round_failed),
            "distinct_sources": len(usable_sources),
        },
        "claim_scores": scores,
        "supported_claims": sorted(c for c, v in verdict_of.items() if v == "supported"),
        "partially_supported_claims": sorted(
            c for c, v in verdict_of.items() if v == "partially_supported"
        ),
        "unsupported_claims": sorted(c for c, v in verdict_of.items() if v == "unsupported"),
        "contradicted_claims": sorted(c for c, v in verdict_of.items() if v == "contradicted"),
        "contradictions": contradictions,
        "outdated_sources": outdated,
        "sources_needing_verification": needs_check,
        "recommended_queries": recommended_queries,
        "gaps": gaps[:10],
    }


def verdict_model(raw: dict[str, Any]) -> ClaimVerdict:
    return ClaimVerdict.model_validate(raw)
