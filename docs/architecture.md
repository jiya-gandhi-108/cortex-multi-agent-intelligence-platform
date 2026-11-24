# Cortex Architecture

Status: complete through Phase 7 (deployment). See also agent-design.md, evaluation.md,
security.md and deployment.md.

## 1. System overview

```
Next.js (Vercel) ──REST + SSE──▶ FastAPI (Render) ──▶ PostgreSQL (Supabase)
                                   │      │              ├─ app tables (SQLAlchemy)
                                   │      │              └─ LangGraph checkpoints
                                   │      └──▶ Redis (rate limits, SSE event fan-out)
                                   └──▶ LangGraph workflow ──▶ Groq (primary) / Gemini
                                                          └──▶ Tavily or Exa + URL extractor
                                   LangSmith ◀── traces
```

## 2. Backend layers

| Layer | Package | Responsibility | May depend on |
|---|---|---|---|
| API | `app/api` | HTTP, auth dependencies, request/response schemas, SSE | services, schemas |
| Services | `app/services` | Use cases: create project, start/cancel run, export report | repositories, graph |
| Graph | `app/graph` | LangGraph state, nodes, edges, routing, limits | agents, tools |
| Agents | `app/agents` | One prompt + output schema + validation per agent | llm, schemas |
| Tools | `app/tools` | Search, URL extraction, metadata, evidence collection | core |
| Repositories | `app/repositories` | All SQL; every query scoped by `user_id` | models |
| Models / Schemas | `app/models`, `app/schemas` | ORM tables / Pydantic contracts | — |
| Core | `app/core` | Settings, logging, security primitives | — |
| Monitoring / Evaluation | `app/monitoring`, `app/evaluation` | Token/cost accounting, tracing; benchmark suite | graph |

Rule: API handlers never touch SQLAlchemy directly, and agents never touch the database.
This keeps agents unit-testable with a mocked LLM and nothing else.

## 3. Key decisions

**psycopg 3 as the only Postgres driver.** SQLAlchemy's async engine and
`langgraph-checkpoint-postgres` both support it, so one driver serves app tables and
workflow checkpoints. Supabase/Render URLs (`postgres://…`) are normalised in settings.

**Groq as primary LLM, via the official `groq` SDK.** Agents call a thin `LLMClient`
protocol with Groq and Gemini implementations. Structured output uses Groq's strict
`json_schema` mode (supported on `openai/gpt-oss-120b` / `-20b`), which requires every
property in `required`, optional fields as `T | null`, and `additionalProperties: false`
on every object; a schema adapter converts Pydantic models to that shape, and the
response is re-validated with Pydantic regardless. Two model tiers: 120b for planning,
analysis and report writing; 20b for high-volume evidence extraction.

**Designed around Groq's free-plan limits** (base figures from Groq's docs: 30 RPM,
8K tokens/min, 200K tokens/day per model). Consequences: a client-side token-bucket
throttle queues calls instead of tripping 429s; 429 responses honour `retry-after`;
retrieved page content is aggressively trimmed before it reaches a prompt; the default
per-project token budget is 120K; and parallel agent fan-out is bounded by the throttle,
so on the free plan "parallel" branches largely serialise. All limits are configurable
for paid plans.

**Search is a pluggable provider** (`SEARCH_PROVIDER=tavily|exa`), both called over REST
with `httpx` — no vendor SDKs, uniform timeouts and error handling. LangChain is not needed for anything LangGraph and the SDKs don't already
cover, which follows the brief's "only where useful" constraint.

**Workflow runs in-process as an asyncio task, with Postgres checkpoints.** A separate
queue (Celery/Arq) is unjustified at this scale. Durability comes from the LangGraph
checkpointer: if the process restarts mid-run, the run is marked `interrupted` and can be
resumed from its last checkpoint. Trade-off: a single Render instance bounds concurrency;
documented as a scaling limit.

**SSE over WebSockets.** Progress is server→client only. SSE works through Vercel/Render
proxies, reconnects natively, and needs no extra protocol. Events are published to a Redis
channel per project, so any API instance can serve the stream, and are persisted to
`agent_executions` so a reconnecting client can rebuild state.

**Rate limiting implemented on Redis directly** (fixed window, `INCR` + `EXPIRE`) rather
than adding `slowapi`; ~30 lines, no extra dependency, per-user and per-IP keys.

**Secrets.** `SecretStr` throughout settings; structlog processor redacts sensitive keys
and credential-shaped values (`sk-ant-…`, `tvly-…`, JWTs, bearer tokens) before any sink.
Production refuses to boot with the default or a short JWT secret.

## 4. Workflow (implemented; details in agent-design.md)

```
START → planner → dispatch ─┬─ research ─┐
                            ├─ competitor ┼→ collect_evidence → verify ─┬→ report → quality_check → END
                            └─ market ────┘            ▲                │
                                                       └── research ◀───┘ (insufficient evidence,
                                                                          iteration < max)
```

* **Planner output is validated twice**: Pydantic shape, then semantic checks — agent
  names must be in the registry, dependencies must reference existing task ids, and the
  dependency graph must be acyclic. One repair attempt with the validation errors fed
  back, then a hard failure.
* **Parallelism** uses LangGraph `Send` for tasks whose dependencies are satisfied; market
  analysis depends on research/competitor output, so it runs in the next superstep.
* **Bounded loops**: `iteration` in state, capped by `max_research_iterations`. Retries
  are implemented in the node wrapper rather than LangGraph's `RetryPolicy`, because a
  task that exhausts its retries must be *contained* (marked failed, dependants skipped)
  instead of failing the whole graph, and every attempt must be recorded.
* **Budgets**: every LLM call reports token usage into state; a node refuses to start if
  the project's token or cost budget is exhausted, producing an explicit `budget_exceeded`
  error state rather than a partial report.
* **Cancellation**: a cancel flag (Redis + DB) is checked at every node boundary and the
  asyncio task is cancelled; completed task results stay persisted.
* **Human approval**: optional `interrupt` in a dedicated `approval_gate` node after
  planning, so resuming never re-runs the planner. Survives restarts (Postgres checkpoints).
* **Per-run dependencies** (LLM client, collector, budget, event sink, cancel flag) are
  passed through LangGraph's typed runtime context, so they are never checkpointed.

## 5. Execution and persistence (implemented)

`services/runner.py` owns runs. `POST /start` validates state, checks integrations and
the per-user active-run limit, commits, then launches an asyncio task that runs the graph
with `astream(stream_mode="updates")`:

* **Every graph update is persisted in its own transaction** (tasks upserted, sources
  inserted with `ON CONFLICT DO NOTHING` on `(project, normalized_url)`, executions,
  reports, token/cost totals, stage → status). The graph never touches the database,
  and a crash loses at most the step in flight.
* **Durability** comes from LangGraph's `AsyncPostgresSaver` (own tables in the same
  database, created on startup), thread id `{project_id}:{run}`. On startup any project
  still marked active is set to `interrupted`; `start` on an interrupted project resumes
  the thread from its last checkpoint without repeating completed (paid) steps. On
  graceful shutdown, running work is marked `interrupted` the same way.
* **Re-runs** increment the run number, replace the run's tasks and sources, and keep
  execution history and earlier report versions.
* **Live events** go to a Redis Stream per run (capped, TTL). The SSE endpoint replays and
  follows it with `XREAD BLOCK`, so `Last-Event-ID` reconnects are gap-free and any API
  instance can serve any stream. Publishing is best-effort: the database is the record.
* **Pooler compatibility**: both SQLAlchemy and the checkpointer pool disable server-side
  prepared statements, so Supabase's transaction-mode pooler (PgBouncer) works.
* **Scaling limit (documented trade-off)**: runs execute in the API process, and orphan
  recovery assumes a single instance. Horizontal scaling would move `_run` into a worker
  with a Redis-backed lock per project; the persistence and event layers already support it.

## 6. Frontend (implemented)

Next.js 16 App Router with Cache Components: every page prerenders a static shell, and
runtime parts (current path, route params, data) stream in behind Suspense boundaries.
Pages are client components fed by a small `useApi` hook. Live run views combine the SSE
stream with backstop polling.

**Backend-for-frontend.** The browser never sees the JWT. `app/api/auth/[action]`
exchanges credentials with FastAPI and stores the token in an `httpOnly`, `SameSite=Lax`,
`Secure` (in production) cookie. `app/api/backend/[...path]` forwards requests with the
bearer token and streams responses through unchanged, so SSE and PDF downloads work.

Consequences:
* XSS can't exfiltrate the token.
* The native `EventSource` works, because cookies are same-origin. That also gives
  automatic reconnection with `Last-Event-ID`, which the backend's Redis Streams honour.
  On Vercel, where a function's duration is capped, a long run's stream is cut and the
  browser resumes it without gaps.
* The CSP can stay `connect-src 'self'`.
* Mutating requests are also checked for same origin.

The proxy forwards the browser's IP with a shared secret (`PROXY_SHARED_SECRET`) so the
backend's per-IP login limits still work. The backend ignores the claimed IP without that
secret, because a plain forwarded-for header would be spoofable.

`proxy.ts` (Next 16's renamed middleware) only redirects based on cookie presence; the
backend validates the token on every call, and a 401 clears the cookie.

**Types** are generated from the backend's OpenAPI schema (`scripts/gen_api_types.sh`), so
schema drift fails `tsc`.

**Verification**: TypeScript strict, ESLint (including React's new hooks rules, which
flagged state resets inside effects; they were restructured, not suppressed), production
build, and headless-Chrome walkthroughs of every page and flow against the real backend
with scripted agents. Those walkthroughs found three bugs the unit tests couldn't:
* Status polling was starved by the elapsed-time ticker.
* The event stream didn't reconnect after a plan was approved.
* Mobile panels overflowed horizontally.

## 7. Observability (implemented)

* **LangSmith.** With `LANGSMITH_TRACING=true` and a key, `configure_tracing` exports the
  settings the SDK reads. LangGraph then traces every node and transition. Provider SDK
  calls are wrapped with `traceable(run_type="llm")`, reporting prompt and completion
  tokens in LangSmith's `usage_metadata` format plus estimated cost. Search and page
  fetches are traced as tools. Each run's trace is named `research:{project}:{run}` and
  carries project and run metadata. Evaluation runs are tagged `evaluation`. Tests force
  tracing off.
* **Structured logs** (JSON in production) with request ids, plus one `llm_call` line per
  model call (agent, model, tokens, cost, latency, repair), one `graph_node_completed` per
  workflow transition, tool outcomes and errors. Secrets are redacted before any sink.
* **Cost** comes from `LLM_PRICING_JSON`, never hardcoded. Startup warns about models
  without a configured price, which then read as "unknown" rather than $0. Totals are kept
  per execution, per project and per evaluation run.
* **Execution records** (every attempt: inputs/outputs summary, tokens, cost, timing, error)
  are in Postgres and exposed per project via `/executions` and `/agents`.

## 8. Untrusted content (implemented)

Retrieved web content is data, never instructions. It is wrapped in delimited blocks with
an explicit system rule, stripped of scripts (no JS execution — HTML is parsed, not
rendered), length-capped, and the model can only cite source ids that exist in the
evidence store; citations are validated post-hoc and unknown ids are rejected.

URL extraction is SSRF-hardened: http/https only, DNS resolved once and every resolved IP
checked against private, loopback, link-local, multicast and reserved ranges (IPv4 and
IPv6), connection pinned to the vetted IP, redirects re-validated hop by hop, response size
and time capped.

## 9. Data model (implemented)

Tables per the brief: `users`, `research_projects`, `research_tasks`, `research_sources`,
`agent_executions`, `research_reports`, `user_settings`. UUID keys, `timestamptz`, JSONB for
task results / agent I/O, unique `(project_id, normalized_url)` for source deduplication,
indexes on every foreign key and on `(user_id, created_at)` for dashboards. Isolation is
enforced in repositories: every read joins to the owning project and filters by
`user_id`; cross-user access returns 404, not 403, to avoid leaking existence.

Migrations own only the app tables (plus `alembic_version`). LangGraph creates and
migrates its own `checkpoint*` tables; `migrations/env.py` excludes them from
autogenerate so a future revision can never drop them, and an integration test checks a
full upgrade/downgrade round trip on an empty database leaves them untouched. Every
table has row level security enabled with no policies, which closes Supabase's
auto-generated Data API; the app connects as the table owner and is unaffected.

## 10. Deployment (implemented; steps in deployment.md)

| Piece | Platform | Notes |
|---|---|---|
| Frontend + BFF | Vercel | Route handlers forward to the API with `X-Proxy-Secret`; stream handler `maxDuration` 300 s, client resumes with `Last-Event-ID` |
| API + workflow | Render (Docker, `render.yaml`) | Runs `alembic upgrade head` on start; health check `/health/ready` gates deploys; graceful shutdown 20 s |
| Postgres | Supabase via the shared pooler | Session (5432) or transaction (6543) mode; prepared statements off for both pools; small pools sized for free-tier connection caps |
| Redis | Render Key Value | `noeviction`; non-persistent on free tier, so only rate limits, revocations and event replay live there |
| Tracing | LangSmith | Optional; disabled when no key |

Background runs execute inside the API process. When the instance stops mid-run,
startup marks the run *interrupted* and **Resume** continues from the Postgres checkpoint.
