# Agent Design

Six agents, each with one responsibility, a typed output contract and a measurable
output. Contracts live in `backend/app/schemas/agents.py`; agents in `backend/app/agents/`;
the workflow in `backend/app/graph/`.

| Agent | Input | Output (schema) | Model tier | Measured by |
|---|---|---|---|---|
| Planner | objective, depth, focus areas | `PlanOutput`: domain, requirements, task DAG | reasoning | valid plan rate, repairs |
| Research | task, objective | `SearchQueries` → sources → `ResearchOutput` findings | fast | findings, rejected citations |
| Competitor | task, objective | `SearchQueries` → sources → `CompetitorOutput` | reasoning | competitors, pricing redactions |
| Market analysis | upstream outputs + their sources | `MarketOutput` labelled insights | reasoning | insights, downgraded facts |
| Verification | claims + cited excerpts | `VerificationBatchOutput` verdicts | reasoning | evidence / reliability score |
| Report | verified claims, competitor data, insights | `ReportCore` + `ReportStrategy` | reasoning | invalid citations removed, quality check |

Model tiers map to `GROQ_MODEL_REASONING` (default `openai/gpt-oss-120b`) and
`GROQ_MODEL_FAST` (default `openai/gpt-oss-20b`).

## 1. One path to the LLM

`agents/base.py::structured_call` is the only way an agent calls a model. In order it:

1. refuses prompts above `LLM_MAX_PROMPT_TOKENS` (default 4,500). Builders trim evidence
   to fit; tripping the guard is a bug, not a runtime condition.
2. checks the shared `BudgetTracker` (tokens + cost) **before** spending.
3. requests strict JSON-schema output. `llm/schema.py` converts the Pydantic model: every
   field required, `additionalProperties: false`, validation keywords stripped.
4. validates with Pydantic, then with an optional semantic validator.
5. on any failure, makes **one** repair attempt: the invalid output plus the list of
   errors is sent back. A second failure raises `AgentOutputError`.
6. records usage and emits an `llm_call` event.

Output models may not use defaults (an optional value is `X | None` and still required);
`test_every_llm_output_model_is_strict_schema_compatible` enforces this for every model.

## 2. Planning

Shape errors are impossible by construction: `agent` is an enum of the three assignable
agents and `task_id` must match `task_\d{3}`. Semantic rules are checked in
`validate_plan` and fed back for repair:

* unique task ids; at most `max_tasks` for the depth (quick 4, standard 7, deep 10)
* dependencies must exist, no self-dependency, no cycles (Kahn's algorithm)
* every `market_analysis_agent` task depends on at least one research/competitor task
* at least one evidence-gathering task

## 3. Evidence discipline

URLs never come from a model. The research and competitor agents ask the LLM for search
*queries*, the Evidence Collector runs them (Tavily or Exa), dedupes by normalised URL,
caps two results per domain, fetches pages through the SSRF-safe fetcher and extracts
main content plus publication date. If a page can't be fetched (robots.txt, 403, timeout,
non-HTML), the search provider's own snippet for that URL is kept and marked
`fetched=false`.

Each source gets a citation key derived from a hash of its normalised URL (`S3f9a2c`).
Parallel branches therefore agree on keys without coordinating, and duplicates merge.

After every extraction, cited ids are checked against the task's retrieved set:

* unknown ids are stripped and counted (`rejected_citations`)
* findings / competitors left with no valid source are dropped
* competitor **pricing without a valid pricing source becomes `null` (unknown)**
* a market `verified_fact` with no valid citation is downgraded to `interpretation`;
  estimates are never relabelled as facts

### Prompt injection

Retrieved text is untrusted data. `tools/sanitize.py` strips control, zero-width and
bidi characters, neutralises tags that could close or forge the `<source>` wrapper
(`</source>`, `<system>`, …), and caps length. Every agent that sees retrieved text gets
a system rule that content inside `<source>` blocks must never be followed. The citation
checks above are the backstop: even a successfully injected model can only cite
sources that were actually retrieved.

## 4. Verification scoring

The LLM judges each claim against the excerpts of the sources the claim cites, in batches
of 8 sized to the prompt ceiling. Its verdict is then constrained: a claim can't be
`supported` unless its supporting ids are among the claim's own cited sources (otherwise
it becomes `unsupported`), and contradictions may only reference claims in the same
batch.

The scores are deterministic functions of the verdicts and source metadata. They are a
**heuristic evidence-strength index in [0, 1], not a calibrated probability.**

```
claim_score = verdict_base × corroboration × freshness
  verdict_base   supported 1.0 · partially_supported 0.5 · unsupported 0 · contradicted 0
  corroboration  distinct supporting domains: 1 → 0.7 · 2 → 0.9 · ≥3 → 1.0
  freshness      newest supporting source ≤ SOURCE_MAX_AGE_YEARS → 1.0
                 all dated sources older → 0.8 · no dates known → 0.9

reliability_score = weighted mean over ALL claims        (critical claims weight 2)
evidence_score    = weighted mean over USABLE claims     (supported + partially supported)

sufficient ⇔ evidence_score ≥ 0.60
           ∧ usable claims ≥ 3
           ∧ distinct sources behind usable claims ≥ depth minimum (3 / 6 / 10)
           ∧ ≤ 30% of critical claims checked in the latest round failed
```

Critical claims: high-importance findings, statistics from high-priority tasks, all
pricing claims, and market `verified_fact`s.

**Why two scores, and why "latest round".** A first version scored sufficiency over all
claims. The workflow tests showed this made follow-up research pointless: claims that
failed in round one stay in the average forever, so no amount of new evidence could
reach the threshold. Failed claims are excluded from the report anyway, so sufficiency
is judged on what the report stands on, and the critical-failure test on the newest round.
`reliability_score` still reports how much of what the agents asserted held up, which
feeds the evaluation module's unsupported-claim rate.

The verifier also returns outdated sources, sources needing further verification (cited
by weak claims, or snippet-only sources behind critical claims), contradictions and up to
six recommended search queries.

## 5. Reporting

The LLM writes prose in two calls sized for Groq's free plan (core findings and analysis;
then strategy), using only supported and partially supported claims. Everything that can
be rendered from data is rendered deterministically and cannot be invented:

* methodology (task table, query/source/claim counts)
* competitor comparison table (from the competitor agent's sourced data; gaps shown as *Unknown*)
* evidence-classified insights (verified fact / estimate / interpretation / recommendation)
* source list (only cited, retrieved sources; date, retrieval time, snippet-only flag)
* evidence limitations (unsupported counts, failed tasks, snippet-only and outdated sources)

Inline `[Sxxxxxx]` citations and citation lists are validated against retrieved sources;
invalid ones are removed and counted, and key findings left uncited are dropped.

The **quality check** (a separate graph node) hard-fails on a missing executive summary,
no cited key findings, no cited sources or no recommendations; it allows one report retry.
Soft issues (insufficient evidence, removed citations, incomplete tasks, mostly snippet
sources) become warnings that are shown in the report and the run result.

## 6. Workflow and failure policy

```
START → planner → approval_gate ─Send×ready─→ research | competitor | market
                                                   ↓
          verification ←─(nothing ready)── collect_evidence ─(more ready)─→ Send…
           │      │  └→ plan_followup ─Send research─→ …   (≤ max_research_iterations)
           │      └→ finalize (no verifiable evidence)
           └→ report → quality_check → complete → END
                 ↑          └→ finalize → END
                 └──(1 retry)──┘
```

* **Parallelism**: every task whose dependencies are complete is dispatched in the same
  superstep with `Send`. On Groq's free plan the per-model rate governor will largely
  serialise the LLM calls; the search and fetch work still overlaps.
* **Retries**: transient provider errors are retried inside the LLM client (honouring
  `retry-after`). Agent-level failures (invalid output after repair, exhausted retries,
  search outages) are retried up to `max_agent_retries` with jittered backoff, and each
  attempt is recorded as an execution.
* **Containment**: a task that still fails is marked `failed`, its dependants `skipped`,
  and everything else continues. The report lists the gaps.
* **Fatal conditions** end the run in an explicit error state, never a partial report:
  `budget_exceeded`, `configuration_error` (missing or rejected API key, never retried),
  `planning_failed`, `verification_failed`, `report_failed`, `quality_check_failed`,
  `insufficient_evidence`, `all_tasks_failed`, `plan_rejected` (→ cancelled).
* **Human approval**: `approval_gate` is separate from the planner, so resuming never
  re-runs the paid planning call. It uses LangGraph `interrupt` and is resumable across
  process restarts (tested against the Postgres checkpointer).
* **Cancellation**: a per-run event checked at every node and before every LLM call.
  The checkpoint keeps completed work.
* **Bounds**: follow-up rounds ≤ `max_research_iterations` (≤ 2 tasks each), report
  attempts ≤ 2, LangGraph recursion limit 80, plus token and cost budgets.

## 7. Prompt sizing for Groq's free plan

Groq's documented base limits are 30 requests/min, 8K tokens/min and 200K tokens/day per
model. Every prompt is capped at about 4.5K tokens so that prompt plus completion fits in
one minute's allowance. Per-source excerpts are 1,000–1,400 characters depending on depth.
A *quick* run makes roughly 10–14 LLM calls. Estimate: a *standard* run uses about 50–80K
tokens, so a free-plan key supports a few standard runs per day per model. Raise the
`GROQ_*_PER_MINUTE` settings on a paid plan.

## 8. Testing

`tests/fakes.py` provides a scripted `FakeLLM` that answers by schema name and reads the
source ids out of each prompt (as a real model would), plus a fake collector. Tests cover
plan validation and repair, citation stripping, pricing redaction, fact downgrading, the
scoring formula, report validation and rendering, and complete graph runs: parallel
dispatch order, contained failures, budget exhaustion, bounded follow-up loops, a
follow-up rescue, approval pause/resume/reject, cancellation, configuration errors and
quality-check retry. One integration test round-trips the whole state through Postgres
checkpoints across a simulated restart. `scripts/live_smoke.py` runs one real quick
workflow once API keys are configured.
