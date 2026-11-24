# Evaluation

`backend/app/evaluation/` runs Cortex against a fixed benchmark suite, stores every result in
Postgres (`evaluation_runs`, `evaluation_results`), and writes a report. It never contains
hardcoded or fabricated numbers: every figure comes from a run that actually happened.

## Modes

| Mode | What runs | What the numbers mean |
|---|---|---|
| `live` | Real LLM (Groq/Gemini) and real web search | Cortex's actual research quality, latency and cost |
| `offline` | Scripted agents from `tests/fakes.py` | Only that the workflow and this harness work end to end. Reports carry a banner saying so. CI runs this mode on every push. |

**Live results status: not yet produced.** They need `GROQ_API_KEY` and a search key. On
Groq's free plan (base limits about 200K tokens/day per model), plan on roughly 4–6 quick
benchmarks per day; `--resume` continues a run across days.

## Benchmarks

16 tasks in `benchmarks.py`, covering all seven research domains in the brief: market,
industry, technology, competitor intelligence, business opportunity, product and strategic
decision. They mix geographies (India, EU, US, Southeast Asia, the Gulf) and include two
decision questions (on-premises vs cloud LLMs; build vs buy search).

Each benchmark lists the topics a competent answer must address, with keyword sets. These
are **coverage checklists, not answer keys**: there is no trustworthy ground truth to grade
facts against automatically, and inventing one would make the evaluation dishonest. Factual
accuracy is checked by people (below).

## Metrics

| # | Metric | How it's computed | Type |
|---|---|---|---|
| 1 | Task completion rate | completed tasks / planned tasks; plus workflow completion (run ended `completed`) | automated |
| 2 | Source relevance | share of retrieved sources mentioning ≥1 topic keyword | automated lexical proxy, plus human 1–5 rating |
| 3 | Evidence coverage | share of required topics backed by ≥1 claim verification judged supported or partly supported | automated |
| 4 | Citation correctness | valid citations / all citations attempted (kept + removed from report + stripped at agent stage); valid means it points at a retrieved source | automated |
| 5 | Unsupported claim rate | (unsupported + contradicted) / claims checked | automated (LLM verifier judgements, constrained by deterministic checks) |
| 6 | Agent execution failure rate | failed attempts / all agent executions | automated |
| 7 | Average workflow latency | wall clock per run; mean, median, p95 | automated |
| 8 | Average cost per research task | estimated cost / runs and / tasks, from configured pricing; "unknown" if pricing isn't configured | automated |

Metrics with no data report `n/a`, never a flattering default. Suite averages show `n`, the
number of runs that contributed.

The offline harness run shows the metrics discriminate rather than rubber-stamp. The
scripted agents always return healthcare text, so evidence coverage is 75% on the
healthcare benchmark and 0% on unrelated ones (EV charging, solar). Citation correctness
is 80%, because the fakes deliberately cite non-existent sources and the pipeline catches
and counts every one.

## Human review

Each run writes `human_review.csv`. For each benchmark, a reviewer reads the report
(`reports/<benchmark>.md`) and fills in:

* `source_relevance_1to5`: are the cited sources on topic and credible?
* `report_usefulness_1to5`: would this report help someone make the decision?
* `factual_spot_check`: open three cited claims and check them against their sources: `pass`
  (all three hold), `partial`, or `fail`.
* `reviewer`, `notes`

Import it with `import-review`; the scores are validated, stored with the results, and
summarised in the regenerated report.

## Commands

```bash
cd backend
.venv/bin/python -m app.evaluation list
.venv/bin/python -m app.evaluation run --mode offline                 # harness check (seconds)
.venv/bin/python -m app.evaluation run --mode live --limit 4          # real run (costs tokens)
.venv/bin/python -m app.evaluation run --mode live --resume <run_id>  # continue tomorrow
.venv/bin/python -m app.evaluation report <run_id>
.venv/bin/python -m app.evaluation import-review <run_id> eval_results/<run_id>/human_review.csv
```

Output in `backend/eval_results/<run_id>/`: `report.md`, `results.json`, `human_review.csv`,
`reports/*.md` (git-ignored). With LangSmith enabled, every benchmark run is traced and tagged
`evaluation`.
