# Cortex API

Base path `/api/v1`. Interactive docs at `/docs`, schema at `/openapi.json`.
All authenticated routes take `Authorization: Bearer <access_token>`.

## Conventions

* **Errors** always use one envelope:
  `{"error": {"code": "not_found", "message": "...", "details": ...}, "request_id": "..."}`.
  Codes: `validation_error` (422), `unauthorized` (401), `not_found` (404), `conflict` /
  `invalid_state` (409), `rate_limited` (429, with `Retry-After`), `payload_too_large`
  (413), `internal_error` (500). Validation details never echo submitted values.
* **Ownership**: a project owned by another user returns 404, identical to a missing one.
* **Pagination**: `?limit=` (1–100, default 20) and `?offset=`; responses are
  `{"items": [...], "total", "limit", "offset"}`, newest first.
* **Rate limits** (Redis, fixed 60 s window): every authenticated route is limited per user
  (`RATE_LIMIT_PER_MINUTE`); login/register per IP (one sixth of that, minimum 5).
* **Tracing**: send `X-Request-ID` (8–64 alphanumerics) or one is generated; it is echoed
  in the response header, error bodies and every log line for the request.

## Endpoints (implemented)

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | `{email, password}` → 201 `{user, token}`. Password 8–128 chars, letter + digit. 409 if email exists (case-insensitive). |
| POST | `/auth/login` | `{email, password}` → `{access_token, token_type, expires_in}`. Same 401 for wrong password and unknown email. |
| GET | `/auth/me` | Current user. |
| POST | `/research` | `{objective (20–4000 chars), title?, depth?, focus_areas?, limits?}` → 201 project in `draft`. Limits are clamped to min(server max, user settings, request). |
| GET | `/research` | Paginated list; `?status=` filter. |
| GET | `/research/{id}` | Full project including resolved `config` and `plan`. |
| GET | `/settings` | User settings plus which providers the server has keys for (booleans only) and server limits. |
| PUT | `/settings` | Partial update; only fields present change. Switching provider clears `preferred_model`. |
| GET | `/health` | Liveness. |
| GET | `/health/ready` | Database/Redis reachability and integration presence; 503 if the database is down. |

### Research runs

| Method | Path | Notes |
|---|---|---|
| POST | `/research/{id}/start` | 202. Starts a draft, **re-runs** a completed/failed/cancelled project (new run number, report version kept), or **resumes** an interrupted one from its last checkpoint. 409 if already active, 409 if the per-user active-run limit is reached, 503 `integration_not_configured` with `details.missing` when LLM/search keys are absent. Rate-limited per user per hour. |
| POST | `/research/{id}/approve` | `{approved, reason?}`. Only in `awaiting_approval` (projects created with `limits.require_plan_approval`). Rejection ends the run as `cancelled`. |
| POST | `/research/{id}/cancel` | Stops at the next node/LLM call (hard-cancel after a 5 s grace). Completed work stays persisted. 409 if nothing to cancel. |
| GET | `/research/{id}/status` | Status, stage, run number, duration, tokens, cost, task counts + list, source count, plan, verification/quality summary. |
| GET | `/research/{id}/events` | **Server-Sent Events.** See below. |
| GET | `/research/{id}/tasks` | Tasks of the current run. |
| GET | `/research/{id}/agents` | Per-agent aggregates: executions, succeeded, failed, tokens, cost, average latency. |
| GET | `/research/{id}/executions` | Paginated execution records (every attempt, inputs/outputs summaries, tokens, cost, timing, errors); `?agent=` filter. History is kept across runs. |
| GET | `/research/{id}/sources` | Paginated retrieved sources: citation key, URL, title, domain, publication/retrieval dates, task, fetched-vs-snippet, excerpt. |
| GET | `/research/{id}/report` | Latest report (`?version=` for older): Markdown, structured sections, validation counts, quality result, verification summary. 404 until one exists. |
| GET | `/research/{id}/report/download` | Markdown file. |
| GET | `/research/{id}/report/pdf` | PDF (WeasyPrint). Raw HTML is disabled and no external resource is ever fetched during rendering. |
| GET | `/dashboard` | Totals from the caller's own records: projects by status, completed tasks, active runs, reports, tokens, cost, recent projects and reports. |

### Project status values

`draft → queued → planning → (awaiting_approval) → running → verifying → reporting → completed`,
or `failed` / `cancelled`; `interrupted` when the server stopped mid-run (start again to resume).
`error_message` carries a machine-readable prefix, e.g. `budget_exceeded: …`, `configuration_error: …`,
`planning_failed: …`, `insufficient_evidence: …`, `quality_check_failed: …`.

### Live events (SSE)

`GET /research/{id}/events` with `Authorization: Bearer …` (use a fetch-based SSE client such
as `@microsoft/fetch-event-source`; tokens are deliberately not accepted in query strings,
where proxies and servers log them).

1. First event: `snapshot` (current status, stage, task counts, sources, tokens).
2. Then the run's events in order, each with an `id` (Redis Stream entry id) and `event`
   type: `status`, `stage`, `plan`, `approval_required`, `task_started`, `task_retry`,
   `task_completed`, `task_failed`, `task_skipped`, `tool_call`, `llm_call`, `sources`,
   `verification`, `report`, `error`, `completed`. `data` is JSON:
   `{type, project_id, at, stage, task_key, agent, data}`.
3. The stream closes after a `status` event whose `data.status` is `completed`, `failed`,
   `cancelled`, `interrupted` or `awaiting_approval`. That event is emitted only after the
   final state is committed, so a status fetch right after closing is consistent.
4. Reconnect with `Last-Event-ID` to resume without gaps (any API instance can serve it).
   Finished runs replay their events and close. Keep-alive pings every 15 s.
