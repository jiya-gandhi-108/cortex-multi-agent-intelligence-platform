# Security

Threats Cortex is designed against, the controls in place, and where each is tested.

| Threat | Controls | Tests |
|---|---|---|
| **Prompt injection via web pages** | Retrieved text is sanitised (control, zero-width and bidi characters stripped; forged `<source>`/`<system>` tags neutralised; length-capped) and wrapped in `<source>` blocks under a system rule never to follow instructions inside them. URLs never come from the model. Every citation is checked against the retrieved set; unknown ids are stripped, unsourced prices become unknown, unsourced "facts" are downgraded. Even a fully hijacked model can only cite real retrieved sources. | `test_research_prompt_wraps_untrusted_content`, `test_clean_text_neutralises_injection_vectors`, citation tests in `test_agents.py` |
| **SSRF through URL extraction** | http/https only; standard ports; no credentials in URLs; internal hostnames refused; numeric-IP spellings refused; DNS resolved once and **every** address must be public (IPv4/IPv6, mapped, 6to4, Teredo, metadata ranges). The connection is pinned to the vetted IP, with SNI and Host set to the real hostname. Each redirect is re-vetted (max 5). Env proxies ignored. Size and time capped. PDF rendering fetches nothing at all. | `test_url_safety.py`, fetcher tests in `test_tools.py`, `test_pdf_renders_without_fetching_remote_images` |
| **Unauthorised access to projects** | Projects are only loadable through `(id, user_id)`; another user's project returns 404, never 403. Applies to every run, source, report, event and PDF endpoint. | `test_user_cannot_see_or_list_other_users_projects`, `test_other_users_cannot_touch_runs` |
| **Credential attacks** | Argon2id hashing; constant-time login, so unknown email and wrong password look identical. Per-IP login rate limit using the real client IP; the frontend proxy's forwarded IP is trusted only with a shared secret (constant-time compare). | `test_login_wrong_password_and_unknown_user_look_identical`, `test_auth_endpoints_rate_limited`, `test_client_ip_trusts_forwarded_ip_only_with_shared_secret` |
| **Token theft / replay** | JWT kept in an `httpOnly`, `SameSite=Lax`, `Secure` cookie behind a same-origin proxy (no JS access, confirmed in a browser test). Algorithm pinned (no `alg=none`); `sub`, `exp`, `iat`, `type` and `jti` required. Sign-out revokes the token server-side (Redis deny-list until expiry). | `test_security.py`, `test_logout_revokes_token_server_side` |
| **CSRF** | `SameSite=Lax` cookie plus an Origin check on every mutating proxy request. | proxy code (`app/api/backend/[...path]/route.ts`) |
| **XSS** | React escaping; Markdown rendered with raw HTML disabled (browser and PDF). CSP `default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`. | `test_raw_html_in_markdown_is_escaped`, browser walkthrough under CSP |
| **Excessive API use / runaway agents** | Per-user API rate limit, per-user hourly research-start limit, per-user active-run limit. Per-project token and cost budgets checked before every LLM call. Bounded retries, follow-up rounds, report attempts and recursion. Prompt-size ceiling. Cancellation at every node. | `test_budget_exhaustion_is_an_explicit_error_state`, `test_per_user_active_run_limit`, `test_insufficient_evidence_triggers_bounded_followup_research` |
| **Secret leakage** | `SecretStr` settings; log processor redacts sensitive keys and credential-shaped values (Groq, Anthropic, Tavily, LangSmith, Google keys, JWTs, bearer tokens); validation errors never echo input; API responses report integration *presence* only. Production refuses to boot with a default or short JWT secret. | `test_logging.py`, `test_config.py`, `test_validation_error_envelope_does_not_echo_password` |
| **Vulnerable dependencies** | All dependencies pinned; `pip-audit` and `npm audit` run in CI. At Phase 6, both reported no known vulnerabilities. | CI |

## Deliberate trade-offs

* **Fail-open on Redis outages** for rate limiting and token revocation. Tokens expire in
  60 minutes and research spend is separately bounded by budgets; taking every
  authenticated request down with Redis would be worse. Both are logged when it happens.
* **Single-instance run manager.** Orphan recovery assumes one API process (see
  architecture.md).
* **`'unsafe-inline'` scripts in the CSP.** Next.js hydration needs it without a per-request
  nonce setup; the remaining directives still block remote script and data exfiltration
  (`connect-src 'self'`).

## Reporting a vulnerability

Open a private security advisory on the repository rather than a public issue.
